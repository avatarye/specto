"""Screener rules: aliases, extension sets, globs, raw regex, order and explain."""

from __future__ import annotations

import re

import pytest
from intake_helpers import ALIASES

from specto_core.errors import InvalidArgs
from specto_intake import Rule, Screener, glob_to_regex
from specto_intake.spec import IntakeSpec


@pytest.mark.parametrize(
    ("glob", "yes", "no"),
    [
        ("*.jpg", ["a.jpg"], ["dir/b.jpg", "a.jpgx", "a.png"]),
        ("front/*.jpg", ["front/a.jpg"], ["front/sub/a.jpg", "rear/a.jpg"]),
        ("front/**/*.jpg", ["front/a.jpg", "front/x/y/a.jpg"], ["rear/a.jpg"]),
        ("**/meta/*.csv", ["meta/a.csv", "runs/x/meta/a.csv"], ["meta/sub/a.csv"]),
        ("frame_?.png", ["frame_1.png"], ["frame_10.png"]),
        ("frame_[0-9][0-9].png", ["frame_42.png"], ["frame_4.png"]),
        ("logs/**", ["logs/a", "logs/x/y"], ["log/a"]),
    ],
)
def test_glob_to_regex(glob: str, yes: list[str], no: list[str]):
    rx = re.compile(glob_to_regex(glob))
    for p in yes:
        assert rx.fullmatch(p), (glob, p)
    for p in no:
        assert not rx.fullmatch(p), (glob, p)


def test_rule_coercion():
    assert Rule.coerce("@images") == Rule(pattern="@images", action="include")
    assert Rule.coerce("!*.tmp") == Rule(pattern="*.tmp", action="exclude")
    assert Rule.coerce("- *.tmp") == Rule(pattern="*.tmp", action="exclude")
    assert Rule.coerce("+.json") == Rule(pattern=".json", action="include")
    assert Rule.coerce({"exclude": "re:^meta/"}) == Rule(pattern="re:^meta/", action="exclude")
    assert Rule.coerce({"pattern": "*.md", "action": "include"}).pattern == "*.md"
    with pytest.raises(InvalidArgs):
        Rule.coerce("")


def test_alias_and_extension_rules_are_case_insensitive():
    s = Screener(["@images", ".JSON"], ALIASES)
    assert s.accepts("a/b.JPG") and s.accepts("c.jpeg") and s.accepts("x.json")
    assert not s.accepts("x.csv")
    kinds = [r.kind for r in s.rules]
    assert kinds == ["alias", "ext"]


def test_unknown_alias_is_a_fix_it_error():
    with pytest.raises(InvalidArgs) as e:
        Screener(["@pointclouds"], ALIASES)
    assert "@images" in (e.value.fix or "")


def test_last_matching_rule_wins_and_include_only_semantics():
    s = Screener(["@images", "!front/*", "front/keep.jpg"], ALIASES)
    assert s.accepts("rear/a.jpg")
    assert not s.accepts("front/a.jpg")  # excluded after include
    assert s.accepts("front/keep.jpg")  # re-included later
    assert not s.accepts("rear/a.json")  # no include rule matched
    ex = s.explain("front/a.jpg")
    assert not ex.included and ex.deciding is not None and ex.deciding.index == 1
    assert [r.index for r in ex.hits] == [0, 1]
    assert "rule 1" in ex.reason


def test_exclude_only_screener_includes_the_rest():
    s = Screener(["!*.tmp", "!re:^\\.git/"], ALIASES)
    assert s.accepts("a.jpg") and s.accepts("deep/x.json")
    assert not s.accepts("a.tmp") and not s.accepts(".git/config")
    assert s.explain("a.jpg").reason.startswith("no exclude rule")
    assert Screener([], ALIASES).accepts("anything")


def test_raw_regex_rule_is_searched_and_case_sensitive():
    s = Screener(["re:_\\d{5}\\.jpg$", "!re:^rear/"], ALIASES)
    assert s.accepts("front/x_00042.jpg")
    assert not s.accepts("front/x_00042.JPG")
    assert not s.accepts("rear/x_00042.jpg")
    with pytest.raises(InvalidArgs):
        Screener(["re:("], ALIASES)


def test_spec_normalises_strings_and_validates_keys():
    spec = IntakeSpec.parse(
        {"sources": "runs/**/*.jpg", "screener": "@images", "keys": r"(?P<frame>\d+)_"}
    )
    assert spec.sources[0].root == "runs" and spec.sources[0].pattern == "**/*.jpg"
    assert spec.screener[0].pattern == "@images" and spec.key_fields() == ["frame"]
    with pytest.raises(InvalidArgs):
        IntakeSpec.parse({"sources": ["x"], "keys": "(\\d+)"})  # no named groups
    with pytest.raises(InvalidArgs):
        IntakeSpec.parse({"sources": ["x"], "keys": "(?P<a>\\d+)", "group_by": ["b"]})
    with pytest.raises(InvalidArgs):
        IntakeSpec.parse({"sources": ["x"], "keys": "(?P<a>"})
    with pytest.raises(InvalidArgs):
        IntakeSpec.parse({"sources": []})
    assert IntakeSpec.parse({"sources": ["x"]}).key_fields() == ["stem"]
