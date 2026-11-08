"""SPEC-59: a watcher's change notice names the entries that arrived, so a host can tell
an arrival from anything else a later collection brings. ``added`` counts them all; the
names are capped at ``ARRIVALS_CAP``."""

from __future__ import annotations

from queue import SimpleQueue

from specto_core.links import ARRIVALS_CAP, LinkWatch


def _watch(tables: list[dict[str, str]]) -> tuple[LinkWatch, SimpleQueue]:
    # never started: the test's thread is the watcher's own, so it may run _check_now
    it = iter(tables)
    q: SimpleQueue = SimpleQueue()
    w = LinkWatch(
        "l2",
        lambda link, value, *, root, env: next(it),
        {},
        None,
        root="",
        env={},
        changes=q,
        interval_s=1.0,
    )
    return w, q


def test_a_notice_names_the_entries_that_arrived():
    w, q = _watch(
        [
            {"0:a.jpg": "1:1"},
            {"0:a.jpg": "1:1", "0:b.jpg": "2:2", "0:c.jpg": "3:3"},
            {"0:a.jpg": "1:9", "0:c.jpg": "3:3"},
        ]
    )
    assert w._check_now() is False  # the baseline
    assert w._check_now() is True
    assert q.get_nowait() == ("l2", 2, 0, 0, ["0:b.jpg", "0:c.jpg"])
    assert w._check_now() is True  # one changed, one removed: nothing arrived
    assert q.get_nowait() == ("l2", 0, 1, 1, [])


def test_the_named_entries_are_capped_but_the_count_is_not():
    many = {f"0:{i}.jpg": "1:1" for i in range(ARRIVALS_CAP + 200)}
    w, q = _watch([{}, many])
    w._check_now()
    w._check_now()
    _lid, added, _removed, _changed, arrived = q.get_nowait()
    assert added == ARRIVALS_CAP + 200 and len(arrived) == ARRIVALS_CAP
    assert arrived[0] == "0:0.jpg"
