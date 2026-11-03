"""Seam 2 (SPEC-31): the usd view host's environment light against the stub USD
source, headless. The choice is the view param ``environment`` (one user submit from the
VIEW panel's control, or the agent's), with the sky and HDRI named (``sky``, ``hdri``) and
the sky's controls (``time_of_day``, ``cloudiness``, ``haze``, live while a slider moves,
one submit on release); the host sends them with each ``load`` and on a change, shows
what the source answers (the choice shown, the note saying why it differs, the controls
in effect), keeps them across stages, fetches a sky not cached yet off the GUI thread
with the progress under the control, and reports it all in snap."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
from test_usd_host import health, stages, submit_usd

from specto_app.hosts import HostContext
from specto_app.usd import skies
from specto_app.views.usd_view import USD_MANIFEST, UsdHost
from specto_core import Engine
from specto_core.jobs import InlineJobManager


class FakeFetcher:
    """A sky fetcher that fetches nothing: the test lands a sky by hand."""

    def __init__(self, on_change: Callable[[], None], cache: Path) -> None:
        self.on_change = on_change
        self.cache = cache
        self.asked: list[str] = []
        self.progress: dict[str, tuple[int, int]] = {}
        self.errors: dict[str, str] = {}

    def cached(self, name: str) -> bool:
        if name.startswith(skies.HDRI_PREFIX):
            return skies.hdri_path(name[len(skies.HDRI_PREFIX) :]) is not None
        return skies.package(name, self.cache) is not None

    def ask(self, name: str) -> None:
        self.asked.append(name)
        self.progress[name] = (0, 300_000)

    def status(self, name: str) -> skies.FetchStatus:
        done, total = self.progress.get(name, (0, 0))
        return skies.FetchStatus(
            cached=self.cached(name),
            fetching=name in self.progress and not self.cached(name),
            done=done,
            total=total,
            error=self.errors.get(name, ""),
        )

    def note(self, name: str) -> str:
        if self.cached(name):
            return ""
        if name in self.errors:
            return self.errors[name]
        if name in self.progress:
            return f"fetching {skies.label(name)} {self.progress[name][0] / 1e6:.1f} of 0.3 MB"
        return ""

    def advance(self, name: str, done: int) -> None:
        self.progress[name] = (done, 300_000)
        self.on_change()

    def land(self, name: str) -> None:
        cache_sky(self.cache, name)
        self.on_change()

    def fail(self, name: str, why: str) -> None:
        self.errors[name] = why
        self.progress.pop(name, None)
        self.on_change()


def cache_sky(cache: Path, name: str) -> None:
    """A fetched sky in ``cache``: the fetcher's layout with fake bytes and the real pins."""
    (cache / "src" / "Dynamic").mkdir(parents=True, exist_ok=True)
    stage = cache / "src" / "Dynamic" / f"{name}.usd"
    stage.write_text("#usda 1.0\n", encoding="utf-8")
    with zipfile.ZipFile(cache / f"{name}.usdz", "w") as zf:
        zf.write(stage, f"Dynamic/{name}.usd")
    pins = skies.sky_pins(name)
    (cache / f"{name}.json").write_text(json.dumps({"files": pins}), encoding="utf-8")


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SPECTO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SPECTO_USER_DIR", str(tmp_path / "user"))
    return tmp_path / "user" / "skies"


@pytest.fixture
def fetchers(cache: Path) -> list[FakeFetcher]:
    return []


def make_host(cache: Path, fetchers: list[FakeFetcher], params: dict | None = None) -> UsdHost:
    engine = Engine(project=None, jobs=InlineJobManager())
    submit_usd(engine, params)

    def factory(on_change):
        fetchers.append(FakeFetcher(on_change, cache))
        return fetchers[-1]

    return UsdHost(
        HostContext(engine, None), "kobi", USD_MANIFEST, headless=True, fetcher_factory=factory
    )


@pytest.fixture
def host(cache: Path, fetchers: list[FakeFetcher]):
    h = make_host(cache, fetchers)
    yield h
    h.dispose()


def source_state(host: UsdHost) -> dict:
    return host.snapshot()["source"]


def loaded(host: UsdHost, tmp_path: Path, spin, *names: str) -> None:
    host.receive("data", stages(tmp_path / "w", *(names or ("valley",))))
    spin(until=lambda: health(host)["status"] == "ok")


def test_the_control_lists_the_choices_the_skies_and_the_hdris(host):
    assert [r["environment"] for r in host.bridge.environments] == ["stage", "sky", "hdri"]
    assert host.bridge.environments[1] == {"environment": "sky", "label": "Sky"}
    assert [r["sky"] for r in host.bridge.skies] == list(skies.SKIES)
    assert host.bridge.skies[0] == {"sky": "ClearSky", "label": "Clear sky"}
    hdris = [r["hdri"] for r in host.bridge.hdris]
    assert hdris == ["studio", "overcast", "sunny", "indoor", "urban"]
    assert (host.bridge.environment, host.bridge.sky, host.bridge.hdri) == (
        "stage",
        "ClearSky",
        "sunny",
    )
    assert host.bridge.skyLabel == "Clear sky" and host.bridge.hdriLabel == "Sunny"
    assert host.bridge.environmentNote == "" and not host.bridge.skyControlsOn
    snap = host.snapshot()
    assert (snap["environment"], snap["environment_note"]) == ("stage", "")
    assert (snap["sky"], snap["hdri"]) == ("ClearSky", "sunny")
    assert snap["sky_fetch"] == {"sky": "ClearSky", "cached": False}


def test_picking_a_choice_is_one_submit_and_the_picture_follows(host, cache, tmp_path, spin):
    cache_sky(cache, "ClearSky")
    loaded(host, tmp_path, spin)
    undo_depth = host.ctx.live.undo_info()["depth"]
    host.bridge.setEnvironment("sky")
    assert host.params["environment"] == "sky"
    assert host.ctx.live.undo_info()["depth"] == undo_depth + 1
    spin(until=lambda: host.bridge.environmentShown == "sky")
    assert source_state(host)["environment"] == "sky"
    assert host.bridge.environment == "sky" and host.bridge.skyControlsOn
    host.bridge.setEnvironment("sky")  # the same again: nothing
    assert host.ctx.live.undo_info()["depth"] == undo_depth + 1
    host.bridge.setEnvironment("night")  # not a choice: nothing
    assert host.ctx.live.undo_info()["depth"] == undo_depth + 1
    # the HDRI list: one submit, the name goes to the source
    host.bridge.setHdri("urban")
    assert host.params["hdri"] == "urban"
    assert host.ctx.live.undo_info()["depth"] == undo_depth + 2
    spin(until=lambda: source_state(host).get("hdri") == "urban")
    assert host.bridge.hdriLabel == "Urban"


def test_a_sky_not_cached_is_fetched_and_applies_when_it_lands(host, fetchers, tmp_path, spin):
    loaded(host, tmp_path, spin)
    host.bridge.setEnvironment("sky")
    assert fetchers and fetchers[0].asked == ["ClearSky"]
    spin(until=lambda: host.bridge.environmentNote.startswith("fetching Clear sky"))
    assert host.bridge.environment == "sky"  # the control shows the choice meanwhile
    fetchers[0].advance("ClearSky", 150_000)
    spin(until=lambda: host.bridge.environmentNote == "fetching Clear sky 0.1 of 0.3 MB")
    snap = host.snapshot()
    assert snap["sky_fetch"]["fetching"] and snap["sky_fetch"]["done"] == 150_000
    fetchers[0].land("ClearSky")
    spin(until=lambda: host.bridge.environmentShown == "sky" and host.bridge.environmentNote == "")
    assert host.snapshot()["sky_fetch"]["cached"] is True
    # another sky: fetched too, the first stays
    host.bridge.setSky("Overcast")
    assert host.params["sky"] == "Overcast" and fetchers[0].asked == ["ClearSky", "Overcast"]
    spin(until=lambda: host.bridge.environmentNote.startswith("fetching Overcast"))
    assert host.bridge.skyLabel == "Overcast"
    fetchers[0].land("Overcast")
    spin(until=lambda: host.bridge.environmentNote == "")
    spin(until=lambda: source_state(host).get("sky") == "Overcast")


def test_a_fetch_that_fails_is_said_under_the_control(host, fetchers, tmp_path, spin):
    loaded(host, tmp_path, spin)
    host.bridge.setEnvironment("sky")
    fetchers[0].fail("ClearSky", "cannot fetch Clear sky: offline? (no route)")
    spin(until=lambda: "offline" in host.bridge.environmentNote)
    assert host.snapshot()["environment_note"] == "cannot fetch Clear sky: offline? (no route)"
    assert host.snapshot()["sky_fetch"]["error"]
    host.bridge.setEnvironment("hdri")  # the note is the sky's alone
    spin(until=lambda: host.bridge.environmentNote == "")


def test_the_agent_sets_the_choices_through_the_params(host, cache, tmp_path, spin):
    cache_sky(cache, "NightSky")
    loaded(host, tmp_path, spin)
    doc = host.ctx.live.document()
    doc["views"]["kobi"]["params"].update({"environment": "sky", "sky": "NightSky", "haze": 0.5})
    host.ctx.live.submit(doc, origin="agent:s1", label="night")
    host.on_patch([{"op": "add", "path": "/views/kobi/params/environment"}], {})
    spin(until=lambda: host.bridge.environmentShown == "sky")
    spin(until=lambda: source_state(host).get("sky") == "NightSky")
    assert host.bridge.haze == 0.5 and source_state(host)["sky_controls"]["haze"] == 0.5
    snap = host.snapshot()
    assert (snap["environment"], snap["sky"]) == ("sky", "NightSky")
    assert snap["sky_controls"] == {"time_of_day": 6.93, "cloudiness": 0.0, "haze": 0.5}


def test_a_slider_moves_the_sky_live_and_writes_the_param_on_release(host, cache, tmp_path, spin):
    cache_sky(cache, "ClearSky")
    loaded(host, tmp_path, spin)
    host.set_environment("sky")
    spin(until=lambda: host.bridge.environmentShown == "sky")
    assert host.bridge.timeOfDay == 6.93  # the sky's own, as the source reads it
    undo_depth = host.ctx.live.undo_info()["depth"]
    host.bridge.setTimeOfDay(9.0, False)
    host.bridge.setTimeOfDay(17.5, False)
    assert host.bridge.timeOfDay == 17.5
    spin(until=lambda: source_state(host)["sky_controls"]["time_of_day"] == 17.5)
    assert "time_of_day" not in host.params  # live only, until the release
    assert host.ctx.live.undo_info()["depth"] == undo_depth
    host.bridge.setTimeOfDay(17.5, True)
    assert host.params["time_of_day"] == 17.5
    assert host.ctx.live.undo_info()["depth"] == undo_depth + 1
    host.bridge.setCloudiness(0.8, True)
    spin(until=lambda: source_state(host)["sky_controls"]["cloudiness"] == 0.8)
    assert host.params["cloudiness"] == 0.8 and host.bridge.cloudiness == 0.8
    assert host.snapshot()["sky_controls"]["time_of_day"] == 17.5


def test_the_choices_are_kept_across_stages_and_sent_with_the_load(host, cache, tmp_path, spin):
    cache_sky(cache, "ClearSky")
    loaded(host, tmp_path, spin, "valley", "stage-a")
    host.set_environment("sky")
    host.set_sky_control("haze", 0.3)
    spin(until=lambda: source_state(host)["environment"] == "sky")
    spin(until=lambda: source_state(host)["sky_controls"]["haze"] == 0.3)
    host.step_item(1)
    spin(until=lambda: (source_state(host).get("loaded") or "").endswith("stage-a.usda"))
    # the new stage loads with the choice and the controls: no flash of the stage's own light
    state = source_state(host)
    assert state["environment"] == "sky" and state["sky_controls"]["haze"] == 0.3
    spin(until=lambda: host.bridge.environmentShown == "sky")


def test_what_shows_may_differ_from_the_choice_and_the_note_says_why(
    cache, fetchers, tmp_path, monkeypatch, qapp, spin
):
    """``SPECTO_USD_STUB_NO_MDL`` makes the stub answer the sky as Storm does: the HDRI
    instead, with the note; the control stays on what was asked, the sliders rest."""
    monkeypatch.setenv("SPECTO_USD_STUB_NO_MDL", "1")
    cache_sky(cache, "ClearSky")
    host = make_host(cache, fetchers)
    try:
        loaded(host, tmp_path, spin)
        host.bridge.setEnvironment("sky")
        spin(until=lambda: host.bridge.environmentShown == "hdri")
        assert host.bridge.environment == "sky" and not host.bridge.skyControlsOn
        note = "Storm cannot render the sky's MDL: the HDRI instead"
        assert host.bridge.environmentNote == note
        snap = host.snapshot()
        assert (snap["environment"], snap["environment_note"]) == ("hdri", note)
        host.bridge.setEnvironment("stage")
        spin(until=lambda: host.bridge.environmentShown == "stage")
        assert host.bridge.environmentNote == ""
    finally:
        host.dispose()


def test_a_choice_restored_from_the_document_applies_before_the_first_load(
    cache, fetchers, tmp_path, qapp, spin
):
    cache_sky(cache, "Cirrus")
    h = make_host(cache, fetchers, {"environment": "sky", "sky": "Cirrus", "time_of_day": 15.0})
    try:
        assert (h.bridge.environment, h.bridge.sky, h.bridge.timeOfDay) == ("sky", "Cirrus", 15.0)
        loaded(h, tmp_path, spin)
        spin(until=lambda: source_state(h).get("environment") is not None)
        state = source_state(h)  # sent with the load, not after
        assert state["environment"] == "sky" and state["sky"] == "Cirrus"
        assert state["sky_controls"]["time_of_day"] == 15.0
        assert not fetchers  # cached: nothing to fetch
    finally:
        h.dispose()


def test_the_ground_toggle_is_one_submit_sent_to_the_source(host, cache, tmp_path, spin):
    cache_sky(cache, "ClearSky")
    loaded(host, tmp_path, spin)
    host.set_environment("sky")
    spin(until=lambda: host.bridge.environmentShown == "sky")
    assert host.bridge.ground is False and source_state(host)["ground"] is False
    undo_depth = host.ctx.live.undo_info()["depth"]
    host.bridge.setGround(True)
    assert host.params["ground"] is True
    assert host.ctx.live.undo_info()["depth"] == undo_depth + 1
    spin(until=lambda: source_state(host)["ground"] is True)
    assert host.snapshot()["ground"] is True
    host.bridge.setGround(True)  # the same again: nothing
    assert host.ctx.live.undo_info()["depth"] == undo_depth + 1


def test_each_control_acts_where_the_sky_shown_takes_it(host, cache, tmp_path, spin):
    cache_sky(cache, "ClearSky")
    cache_sky(cache, "CloudySky")
    loaded(host, tmp_path, spin)
    assert not (host.bridge.timeOfDayOn or host.bridge.cloudinessOn or host.bridge.hazeOn)
    host.set_environment("sky")
    spin(until=lambda: host.bridge.environmentShown == "sky")
    assert host.bridge.timeOfDayOn and host.bridge.cloudinessOn and host.bridge.hazeOn
    host.set_sky("CloudySky")  # the textured sky: the time of day and its clouds, no haze
    spin(until=lambda: source_state(host).get("sky") == "CloudySky")
    assert host.bridge.timeOfDayOn and host.bridge.cloudinessOn and not host.bridge.hazeOn
    assert host.snapshot()["sky_controls_offered"] == ["time_of_day", "cloudiness"]


def test_on_storm_the_hdri_standing_in_for_the_sky_is_shown_and_can_be_picked(
    cache, fetchers, tmp_path, monkeypatch, qapp, spin
):
    monkeypatch.setenv("SPECTO_USD_STUB_NO_MDL", "1")
    cache_sky(cache, "ClearSky")
    host = make_host(cache, fetchers)
    try:
        loaded(host, tmp_path, spin)
        assert not host.bridge.hdriStandsIn
        host.bridge.setEnvironment("sky")
        spin(until=lambda: host.bridge.environmentShown == "hdri")
        assert host.bridge.hdriStandsIn and host.bridge.hdriLabel == "Sunny"
        assert not host.bridge.skyControlsOn  # no sky shows: its controls rest
        host.bridge.setHdri("urban")  # picked from the stand-in's list
        spin(until=lambda: source_state(host).get("hdri") == "urban")
        assert host.bridge.environment == "sky" and host.bridge.hdriLabel == "Urban"
    finally:
        host.dispose()


def test_an_hdri_not_here_is_fetched_and_its_progress_said(
    cache, fetchers, tmp_path, monkeypatch, qapp, spin
):
    import specto_web

    monkeypatch.setattr(specto_web, "hdri_source", lambda name, root=None: None)
    host = make_host(cache, fetchers)
    try:
        loaded(host, tmp_path, spin)
        host.bridge.setEnvironment("hdri")
        assert fetchers and fetchers[0].asked == [skies.hdri_key("sunny")]
        spin(until=lambda: host.bridge.environmentNote.startswith("fetching"))
        snap = host.snapshot()
        assert snap["hdri_fetch"]["hdri"] == "sunny" and snap["hdri_fetch"]["fetching"]
    finally:
        host.dispose()
