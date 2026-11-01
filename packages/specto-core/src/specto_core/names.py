"""View names (#79, ADR 0018): a view's id is a short pronounceable name (``kobi``,
``tamu``) that the app hands out; the name IS the id, one handle for the user, the
agent and the document. Names never change once given.

A name is two CV syllables from a restricted alphabet (``NAME_PATTERN``), so the pool is
small enough to read aloud and strict enough that an agent's own key (``v9``,
``new_gallery``, ``gallery2``) is recognisably a placeholder (``is_name``). Repeated
syllables (``baba``) and a few words that read badly or collide with document
vocabulary are left out of the pool.

``fresh_name`` is deterministic: the same canvas and salt give the same name, so a
submit renamed twice lands the same way and tests can predict it, while a different set
of taken names or a different salt moves on through the pool. The file tree is named
like any view (#87); only ``project``, the project source, keeps a fixed id.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any

from .identicon import MIN_DISTANCE, identicon_bits

#: the salt a fresh canvas names its file tree with: every canvas starts with the same
#: tree name (``fege``), but code finds the file tree by its type, never by this (#87)
FILE_TREE_SALT = "file_tree"
#: the project source's fixed id (``ports.PROJECT_ID``): never a placeholder, never renamed
RESERVED = "project"

CONSONANTS = "bdfghklmnprstvz"
VOWELS = "aeiou"
#: the name grammar: exactly two consonant-vowel syllables, lower case
NAME_PATTERN = rf"^[{CONSONANTS}][{VOWELS}][{CONSONANTS}][{VOWELS}]$"
#: what a view key may be in a valid document: a name (the file tree too, #87)
VIEW_ID_PATTERN = NAME_PATTERN

#: words in the grammar that read badly, or that are document vocabulary and would
#: confuse an agent reading ``kobi.data`` next to ``data.data``
BLOCKED: frozenset[str] = frozenset(
    {
        "nazi", "homo", "puta", "puto", "kuso", "baka", "pedo", "pene", "kike", "hobo",
        "dago", "sodo", "tabu", "gogo", "doho", "fuka", "fuku", "nigo", "boko",
        "data", "file", "node", "none", "time", "home", "name", "size", "mode", "code",
        "page", "pane", "demo", "zero", "nine", "five", "base", "tile", "line", "rate",
        "site", "role", "note", "live", "mute", "fake", "hide", "redo", "lose", "move",
        "make", "take", "give", "save", "pure", "sole", "vote", "zone", "lane", "ride",
        "rule", "tone", "game", "fire", "bike", "dome", "cube", "nose", "hole", "pole",
        "tube", "wide", "gate", "late", "mate", "mile", "mine", "pipe", "rope", "side",
        "tape", "vase", "wake", "dude", "mojo", "dodo", "kudo", "zulu", "tofu", "sake",
    }
)  # fmt: skip

_NAME = re.compile(NAME_PATTERN)
#: a link id (``canvas_document.LINK_ID_PATTERN``); anything else is a placeholder
_LINK_ID = re.compile(r"l[0-9]+")


def _next_link_id(ids: Iterable[str]) -> str:
    """The next free ``l<n>`` (``canvas_document.next_id``, which imports this module)."""
    used = [int(i[1:]) for i in ids if _LINK_ID.fullmatch(i)]
    return f"l{max(used, default=0) + 1}"


def is_name(value: str) -> bool:
    """True for a generated-style name (two CV syllables, no repeated syllable, not
    blocked); False for placeholders and for the file tree's old fixed id ``files``."""
    return bool(_NAME.fullmatch(value)) and value[:2] != value[2:] and value not in BLOCKED


def all_names() -> list[str]:
    """The whole pool, sorted (about 5500 names)."""
    out = []
    for c1 in CONSONANTS:
        for v1 in VOWELS:
            for c2 in CONSONANTS:
                for v2 in VOWELS:
                    n = c1 + v1 + c2 + v2
                    if is_name(n):
                        out.append(n)
    return out


_POOL: list[str] = all_names()


def fresh_name(taken: Iterable[str], salt: str = "") -> str:
    """One name not in ``taken``, picked by hashing the taken set and ``salt`` into the
    pool and probing forward past taken names and past names whose identicon is a near
    twin of a taken name's (#81: fewer than ``MIN_DISTANCE`` cells apart). Deterministic
    for equal inputs. On a canvas so full that every free name is a twin of some taken
    one, the first free name is taken anyway."""
    used = set(taken)
    if len(used) >= len(_POOL):
        raise ValueError("the name pool is exhausted")  # 5500 views on one canvas
    digest = hashlib.sha256(("|".join(sorted(used)) + "#" + salt).encode("utf-8")).digest()
    start = int.from_bytes(digest[:4], "big") % len(_POOL)
    grids = [identicon_bits(n) for n in used]
    first_free: str | None = None
    for step in range(len(_POOL)):
        name = _POOL[(start + step) % len(_POOL)]
        if name in used:
            continue
        first_free = first_free or name
        bits = identicon_bits(name)
        if all(bin(bits ^ g).count("1") >= MIN_DISTANCE for g in grids):
            return name
    assert first_free is not None
    return first_free


def fresh_names(taken: Iterable[str], n: int, salt: str = "") -> list[str]:
    """``n`` distinct names none of which is in ``taken``."""
    used = set(taken)
    out: list[str] = []
    for k in range(n):
        name = fresh_name(used, f"{salt}:{k}")
        used.add(name)
        out.append(name)
    return out


# ---------------------------------------------------------------------------------------
# Renaming views in a document (JSON form): keys, link ends and the root view move
# ---------------------------------------------------------------------------------------


def rename_views(data: dict[str, Any], mapping: dict[str, str]) -> None:
    """Rename views in place: the ``views`` keys (order kept), every link's source and
    target, and ``root_view``. Ids not in ``mapping`` stay."""
    if not mapping:
        return
    views = data.get("views")
    if isinstance(views, dict):
        data["views"] = {mapping.get(vid, vid): view for vid, view in views.items()}
    links = data.get("links")
    if isinstance(links, dict):
        for link in links.values():
            if not isinstance(link, dict):
                continue
            for end in ("source", "target"):
                ref = link.get(end)
                if isinstance(ref, dict) and ref.get("view") in mapping:
                    ref["view"] = mapping[ref["view"]]
    root = data.get("root_view")
    if isinstance(root, str) and root in mapping:
        data["root_view"] = mapping[root]


def _view_keys(data: dict[str, Any]) -> list[str]:
    views = data.get("views")
    return [k for k in views if isinstance(k, str)] if isinstance(views, dict) else []


def rename_placeholders(
    data: dict[str, Any],
    existing: Iterable[str],
    existing_links: Iterable[str] = (),
    retired: Iterable[str] = (),
) -> dict[str, str]:
    """Submit time (ADR 0018): a view keyed by anything that is neither a view of the
    running canvas (``existing``) nor in name form is a new view under a placeholder
    (``v9``, ``new_gallery``); it gets a fresh name and its links follow. Likewise a
    link keyed by anything that is neither a live link (``existing_links``) nor ``l<n>``
    gets the next free ``l<n>`` (#98); nothing refers to a link by id, so only the key
    changes. ``retired``: view names and link ids the canvas handed out before and no
    longer has (removed, the undo history still names them): never handed out again, so
    a new view is not mistaken for the one the user removed (#169). Returns
    ``{placeholder: name or link id}``; the document is renamed in place."""
    keys = _view_keys(data)
    known = set(existing)
    retired = set(retired)
    placeholders = [k for k in keys if k not in known and k != RESERVED and not is_name(k)]
    mapping: dict[str, str] = {}
    if placeholders:
        taken = set(keys) | known | retired
        for key in placeholders:
            name = fresh_name(taken, key)
            taken.add(name)
            mapping[key] = name
        rename_views(data, mapping)
    links = data.get("links")
    if isinstance(links, dict):
        live_links = set(existing_links)
        new = [k for k in links if k not in live_links and not _LINK_ID.fullmatch(str(k))]
        if new:
            used = [k for k in links if isinstance(k, str)] + list(live_links) + sorted(retired)
            renamed: dict[str, str] = {}
            for key in new:
                lid = _next_link_id(used)
                used.append(lid)
                renamed[key] = lid
            data["links"] = {renamed.get(k, k): v for k, v in links.items()}
            mapping.update(renamed)
    return mapping


def rename_legacy_ids(data: dict[str, Any]) -> dict[str, str]:
    """Load time (ADR 0018): a canvas saved before names has ``v<n>`` ids, and one saved
    before #87 has the file tree at the fixed id ``files``; every view not in name form
    gets a name, once, the file tree like any other. Returns the mapping (empty when
    nothing was legacy); the document is renamed in place."""
    keys = _view_keys(data)
    legacy = [k for k in keys if k != RESERVED and not is_name(k)]
    if not legacy:
        return {}
    taken = set(keys)
    mapping: dict[str, str] = {}
    for key in legacy:
        name = fresh_name(taken, key)
        taken.add(name)
        mapping[key] = name
    rename_views(data, mapping)
    return mapping


__all__ = [
    "BLOCKED",
    "CONSONANTS",
    "FILE_TREE_SALT",
    "NAME_PATTERN",
    "VIEW_ID_PATTERN",
    "VOWELS",
    "all_names",
    "fresh_name",
    "fresh_names",
    "is_name",
    "rename_legacy_ids",
    "rename_placeholders",
    "rename_views",
]
