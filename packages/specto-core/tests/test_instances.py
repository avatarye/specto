"""#187 seam 1: instance names and the registry of running instances."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from specto_core import instances
from specto_core.errors import NotFound
from specto_core.instances import (
    BLOCKED_INSTANCE,
    Instance,
    find_instance,
    instance_name,
    is_instance_name,
    list_instances,
    pid_alive,
    register,
    unregister,
)
from specto_core.names import is_name


@pytest.fixture(autouse=True)
def user_dir(tmp_path, monkeypatch):
    d = tmp_path / "user"
    monkeypatch.setenv("SPECTO_USER_DIR", str(d))
    return d


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _inst(name: str, project: str, pid: int | None = None) -> Instance:
    return Instance(
        name=name,
        project=project,
        project_name=os.path.basename(project),
        pid=os.getpid() if pid is None else pid,
        endpoint={"kind": "pipe", "address": f"specto-{name}", "pid": os.getpid()},
    )


def test_names_are_five_letter_cvcvc_and_never_view_names(tmp_path):
    name = instance_name(tmp_path / "a")
    assert len(name) == 5 and is_instance_name(name)
    assert not is_name(name)  # view names are 4 letters
    assert not is_instance_name("kobi") and not is_instance_name("rasell")
    assert all(is_instance_name(n) is False for n in BLOCKED_INSTANCE)
    assert all(instances._INSTANCE.fullmatch(n) for n in BLOCKED_INSTANCE)


def test_name_is_stable_per_project_and_kept_in_the_user_dir(tmp_path, user_dir):
    a1 = instance_name(tmp_path / "a")
    b = instance_name(tmp_path / "b")
    assert instance_name(tmp_path / "a") == a1  # a restart keeps the address
    assert a1 != b
    kept = json.loads((user_dir / "instance-names.json").read_text(encoding="utf-8"))
    assert set(kept["projects"].values()) == {a1, b}


def test_a_name_held_by_another_live_instance_draws_a_new_one(tmp_path, user_dir):
    a = instance_name(tmp_path / "a")
    register(_inst(a, str(tmp_path / "other")))  # someone else runs under a's name
    again = instance_name(tmp_path / "a")
    assert again != a and is_instance_name(again)
    assert instance_name(tmp_path / "a") == again  # and that one is kept now


def test_two_instances_listed_and_found(tmp_path):
    register(_inst("rasel", str(tmp_path / "a")))
    register(_inst("tomvi", str(tmp_path / "b")))
    live = list_instances()
    assert [i.name for i in live] == ["rasel", "tomvi"]
    assert live[0].url() == "pipe://specto-rasel"
    assert find_instance("TOMVI").project == str(tmp_path / "b")
    with pytest.raises(NotFound) as e:
        find_instance("bakov")
    assert "rasel, tomvi" in (e.value.fix or "")


def test_a_stale_entry_is_swept_by_readers(tmp_path, user_dir):
    dead = _dead_pid()
    assert not pid_alive(dead)
    register(_inst("rasel", str(tmp_path / "a")))
    register(_inst("tomvi", str(tmp_path / "b"), pid=dead))
    assert [i.name for i in list_instances(sweep=False)] == ["rasel"]
    assert (user_dir / "instances" / "tomvi.json").exists()
    assert [i.name for i in list_instances()] == ["rasel"]
    assert not (user_dir / "instances" / "tomvi.json").exists()
    # a stale holder never blocks the kept name
    kept = instance_name(tmp_path / "c")
    register(_inst(kept, str(tmp_path / "d"), pid=dead))
    assert instance_name(tmp_path / "c") == kept


def test_unregister_only_removes_its_own_entry(tmp_path, user_dir):
    register(_inst("rasel", str(tmp_path / "a")))
    assert not unregister("rasel", pid=os.getpid() + 1)
    assert unregister("rasel", pid=os.getpid())
    assert list_instances() == []
    assert not unregister("rasel")


def test_pid_alive():
    assert pid_alive(os.getpid())
    assert not pid_alive(0)


# -- #190: a sender verified from the caller's nimitz labels, else claimed ------------------------
def test_caller_labels_are_the_nimitz_member_labels_that_are_set():
    env = {"NIMITZ_ID": "FLEET/SQUAD/SHIP", "NIMITZ_ROLE": "ship", "NIMITZ_FLEET": "FLEET"}
    assert instances.caller_labels(env) == {
        "NIMITZ_ID": "FLEET/SQUAD/SHIP",
        "NIMITZ_ROLE": "ship",
    }
    assert instances.caller_labels({"PATH": "x"}) == {}


@pytest.mark.parametrize(
    ("labels", "member"),
    [
        ({"NIMITZ_ID": "FLEET", "NIMITZ_ROLE": "admiral"}, "FLEET"),
        ({"NIMITZ_ID": "FLEET/SQUAD", "NIMITZ_ROLE": "commodore"}, "FLEET/SQUAD"),
        ({"NIMITZ_ID": "FLEET/SQUAD/SHIP", "NIMITZ_ROLE": "ship"}, "FLEET/SQUAD/SHIP"),
        ({"NIMITZ_ID": "FLEET/SQUAD/SHIP", "NIMITZ_ROLE": "admiral"}, None),  # disagree
        ({"NIMITZ_ID": "FLEET/SQUAD/SHIP"}, None),  # partial
        ({"NIMITZ_ID": "fleet/squad", "NIMITZ_ROLE": "commodore"}, None),  # not an id
        ({"NIMITZ_ID": "A/B/C/D", "NIMITZ_ROLE": "ship"}, None),
        ({}, None),
        (None, None),
    ],
)
def test_verified_member_follows_nimitz_s_rule(labels, member):
    assert instances.verified_member(labels) == member


def test_a_sender_is_verified_only_when_the_labels_name_it():
    labels = {"NIMITZ_ID": "FLEET/SQUAD/SHIP", "NIMITZ_ROLE": "ship"}
    assert instances.sender_verified("FLEET/SQUAD/SHIP", labels)
    assert not instances.sender_verified("FLEET/SQUAD/OTHER", labels)
    assert not instances.sender_verified("the user", labels)
    assert not instances.sender_verified("FLEET/SQUAD/SHIP", None)
