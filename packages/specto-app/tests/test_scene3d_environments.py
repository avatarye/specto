"""#175: the 3D view's bundled HDRI environments, headless. The committed manifest
(``web/3d/environments.json``: Poly Haven CC0 1k HDRIs pinned by sha256) and the build
step that vendors them (``build.py``: fetched once, checked, prefiltered); seam 1, what
the agent reads (describe, the session reference) and what a submit of the four view
params resolves to; seam 2, the host telling its page which environments there are and
where their vendored files are on its own host (never the network), the page's own
picks landing as one user submit and reaching the page back as params."""

from __future__ import annotations

import gc
import hashlib
import importlib.util
import io
import json
import re
from pathlib import Path

import pytest
from scene3d_helpers import seed_scans
from web_helpers import Sink

import specto_intake  # noqa: F401  (registers the intaker resolver)
import specto_web
from specto_app.hosts import HostContext, HostRegistry, clear_pending
from specto_app.prompt.reference import reference_text
from specto_app.views.scene3d_view import (
    ENVIRONMENTS,
    SCENE3D_MANIFEST,
    Scene3DHost,
    environment_entries,
    factory,
)
from specto_app.web.runtime import reset_runtime
from specto_core import Engine, Project
from specto_core.jobs import InlineJobManager

WEB = Path(specto_web.__file__).resolve().parents[2] / "web"
BUILD = WEB.parent / "build.py"
NAMES = ["studio", "overcast", "sunny", "indoor", "urban"]
ENV_PARAMS = {
    "environment": "sunny",
    "environment_intensity": 1.5,
    "environment_background": True,
    "environment_rotation": 90.0,
}
REGISTRIES: list[HostRegistry] = []


def load_build():
    spec = importlib.util.spec_from_file_location("specto_web_build", BUILD)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- the asset manifest ------------------------------------------------------------------------
def test_the_manifest_bundles_five_small_cc0_poly_haven_hdris():
    manifest = specto_web.environments_manifest()
    assert manifest["licence"] == "CC0-1.0"
    assert manifest["licence_url"] == "https://polyhaven.com/license"
    envs = manifest["environments"]
    assert [e["name"] for e in envs] == NAMES  # studio, outdoor x2, indoor, urban
    for e in envs:
        asset = e["asset"]
        assert e["url"] == f"https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/1k/{asset}_1k.hdr"
        assert e["page"] == f"https://polyhaven.com/a/{asset}"
        assert re.fullmatch(r"[0-9a-f]{64}", e["sha256"]), e["name"]
        assert 0 < e["size"] <= 2_000_000, "1k files, 2 MB or less"
        assert e["author"] and e["label"] and e["look"]
    assert manifest["cube_size"] == 256


def test_the_build_fetches_each_hdri_once_and_refuses_one_off_its_pin(tmp_path, monkeypatch):
    build = load_build()
    body = b"#?RADIANCE\nfake\n"
    manifest = {
        "cube_size": 256,
        "environments": [
            {
                "name": "studio",
                "url": "https://dl.polyhaven.org/x/studio_1k.hdr",
                "sha256": hashlib.sha256(body).hexdigest(),
            },
        ],
    }
    monkeypatch.setattr(build, "HDRI_CACHE", tmp_path / "cache")
    served: list[str] = []

    def urlopen(req, timeout=0):
        served.append(req.full_url)
        return io.BytesIO(body)

    monkeypatch.setattr(build.urllib.request, "urlopen", urlopen)
    got = build.fetch_hdri(manifest)
    assert got["studio"].read_bytes() == body and served == [manifest["environments"][0]["url"]]
    build.fetch_hdri(manifest)
    assert len(served) == 1, "a verified file in the cache is not fetched again"

    manifest["environments"][0]["sha256"] = "0" * 64
    with pytest.raises(SystemExit, match="sha256"):
        build.fetch_hdri(manifest)
    assert not got["studio"].exists(), "a file off its pin is not kept"


def test_the_build_stamp_follows_the_pins(tmp_path):
    build = load_build()
    m = build.hdri_manifest()
    stamp = build.hdri_stamp(m)
    assert re.fullmatch(r"[0-9a-f]{16}", stamp) and build.hdri_stamp(m) == stamp
    m2 = json.loads(json.dumps(m))
    m2["environments"][0]["sha256"] = "f" * 64
    assert build.hdri_stamp(m2) != stamp, "a changed HDRI bakes again"
    m2 = json.loads(json.dumps(m))
    m2["cube_size"] = 128
    assert build.hdri_stamp(m2) != stamp, "so does a changed cube size"


def test_the_vendored_files_are_page_relative_prefiltered_first_and_none_until_built(
    tmp_path, monkeypatch
):
    root = tmp_path / "web"
    (root / "3d").mkdir(parents=True)
    (root / "bridge.js").write_text("", encoding="utf-8")
    (root / "3d" / "environments.json").write_bytes((WEB / "3d" / "environments.json").read_bytes())
    hdri = root / "vendor" / "hdri"
    hdri.mkdir(parents=True)
    (hdri / "studio.env").write_bytes(b"env")
    (hdri / "sunny.hdr").write_bytes(b"hdr")  # no WebGL2 on the build machine: the raw .hdr
    (hdri / "indoor.hdr").write_bytes(b"hdr")
    (hdri / "indoor.env").write_bytes(b"env")
    monkeypatch.setenv(specto_web.ENV_VAR, str(root))
    by = {e["name"]: e for e in environment_entries()}
    assert list(by) == NAMES
    assert by["studio"]["url"] == "/vendor/hdri/studio.env"
    assert by["sunny"]["url"] == "/vendor/hdri/sunny.hdr"
    assert by["indoor"]["url"] == "/vendor/hdri/indoor.env", "the prefiltered file wins"
    assert by["overcast"]["url"] is None and by["urban"]["url"] is None
    assert by["studio"]["author"] == "Sergej Majboroda"
    assert by["studio"]["page"] == "https://polyhaven.com/a/studio_small_09"
    assert set(by["studio"]) == {"name", "label", "look", "author", "page", "url"}


# -- seam 1: what the agent reads and what a submit resolves to ---------------------------------
@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    reset_runtime()
    clear_pending()
    proj = Project.init(tmp_path / "proj", "scans")
    seed_scans(proj.root)
    eng = Engine(project=proj, jobs=InlineJobManager())
    yield eng
    for reg in REGISTRIES:
        reg.dispose_all()
    REGISTRIES.clear()
    eng.links.close()
    reset_runtime()
    gc.collect()


def submit_3d(engine: Engine, params: dict | None = None, *, origin: str = "agent:test") -> None:
    doc = engine.live.document()
    doc["views"]["kobi"] = {
        "type": "3d",
        "label": "Scans",
        "layout": {"position": [300, 0], "size": [800, 600]},
        "params": dict(params or {}),
    }
    doc["links"]["l2"] = {
        "kind": "intaker",
        "scope": "pinned",
        "label": "scans",
        "source": {"view": "project", "port": "all_files"},
        "target": {"view": "kobi", "port": "data"},
        "params": {"sources": ["scans"], "screener": ["@3d"]},
        "summary": {"sentence": "The 3D view shows every scan under scans/."},
    }
    engine.live.submit(doc, origin=origin, label="3d")


def register_3d(engine: Engine) -> HostRegistry:
    """The 3d plugin known to the engine, as the app's registry makes it."""
    reg = HostRegistry(HostContext(engine, None), load_entry_points=False)
    reg.add_factory(factory())
    REGISTRIES.append(reg)
    return reg


def test_describe_view_3d_names_the_environments_and_their_params(engine: Engine):
    register_3d(engine)
    info = engine.call("describe", {"target": "view:3d"})["info"]
    assert {k: info["defaults"][k] for k in ENV_PARAMS} == {
        "environment": "none",
        "environment_intensity": 1.0,
        "environment_background": False,
        "environment_rotation": 0.0,
    }
    props = info["state_schema"]["properties"]
    assert props["environment"]["enum"] == ["none", *NAMES]
    assert ENVIRONMENTS == tuple(NAMES)
    assert (
        props["environment_intensity"]["minimum"],
        props["environment_intensity"]["maximum"],
    ) == (0, 4)
    assert (props["environment_rotation"]["minimum"], props["environment_rotation"]["maximum"]) == (
        0,
        360,
    )
    assert props["environment_background"]["type"] == "boolean"
    assert "environment" in SCENE3D_MANIFEST.doc


def test_the_session_reference_gives_the_agent_the_environment_choices(engine: Engine):
    register_3d(engine)
    line = next(x for x in reference_text(engine.call).splitlines() if x.startswith("- 3d: "))
    card = json.loads(line[len("- 3d: ") :])
    assert card["params"]["environment"].startswith("none|studio|overcast|sunny|indoor|urban. ")
    assert card["params"]["environment_intensity"].startswith("0..4. ")
    assert card["params"]["environment_rotation"].startswith("0..360. ")


def test_an_agents_environment_params_resolve_into_the_document(engine: Engine):
    register_3d(engine)
    submit_3d(engine, ENV_PARAMS)
    assert engine.live.data["views"]["kobi"]["params"] == ENV_PARAMS


# -- seam 2: the host and its page ---------------------------------------------------------------
def make_host(engine: Engine) -> tuple[HostContext, Scene3DHost]:
    ctx = HostContext(engine, None)
    reg = HostRegistry(ctx, load_entry_points=False)
    reg.add_factory(factory())
    REGISTRIES.append(reg)
    host = reg.host("kobi")
    assert isinstance(host, Scene3DHost)
    return ctx, host


def test_the_scene_payload_lists_the_environments_on_the_pages_own_host(engine: Engine):
    submit_3d(engine, ENV_PARAMS)
    _ctx, host = make_host(engine)
    scene = host.scene_payload()
    assert [e["name"] for e in scene["environments"]] == NAMES
    for e in scene["environments"]:
        f = specto_web.environment_file(e["name"])
        # build.py vendors them beside the other web assets; nothing points off the host
        assert e["url"] == (f"/vendor/hdri/{f.name}" if f else None)
    assert {k: scene["params"][k] for k in ENV_PARAMS} == ENV_PARAMS
    assert host.snapshot()["environment"] == "sunny"
    sink = Sink(host.bridge)
    host.bridge.ready()
    assert sink.last("scene")["environments"] == scene["environments"]


def test_the_pages_environment_picks_are_one_user_submit_and_reach_it_back(engine: Engine):
    submit_3d(engine)
    _ctx, host = make_host(engine)
    sink = Sink(host.bridge)
    host.bridge.ready()
    n = len(engine.live.history)
    res = json.loads(host.bridge.setParams(json.dumps({"environment": "studio"})))
    assert res["ok"], res
    res = json.loads(
        host.bridge.setParams(
            json.dumps({"environment_intensity": 0.5, "environment_rotation": 45})
        )
    )
    assert res["ok"], res
    res = json.loads(host.bridge.setParams(json.dumps({"environment_background": True})))
    assert res["ok"], res
    assert len(engine.live.history) == n + 3
    assert all(r.origin == "user" for r in engine.live.history[n:])
    assert engine.live.history[-3].label == "Scans: environment"
    params = engine.live.data["views"]["kobi"]["params"]
    assert {k: params[k] for k in ENV_PARAMS} == {
        "environment": "studio",
        "environment_intensity": 0.5,
        "environment_background": True,
        "environment_rotation": 45,
    }
    host.flush()
    assert sink.params[-1]["environment"] == "studio"
    # an agent's switch reaches the page the same way
    doc = engine.live.document()
    doc["views"]["kobi"]["params"]["environment"] = "none"
    engine.live.submit(doc, origin="agent:s1", label="no hdri")
    host.flush()
    assert sink.params[-1]["environment"] == "none"
