"""The project's scratch pad (#184): named pages of markdown, kept in
``<project>/.specto/scratch``, that the user drops into, pastes into and edits, and the
agent reads and writes.

``pages.json`` holds the pages in order (``id``, ``name``), the active page and the
counters. Each page is one markdown file ``<page id>.md``; the pictures dropped on it are
png files in ``<page id>.assets/``, embedded with ``![label](<page id>.assets/<name>.png)``.
Links point at files with paths relative to the page (``../../runs/front/00042.jpg``, so
the note reads the same in any markdown viewer); a file outside the project is a
``file:///`` URL, and never a network path: every path that reaches the pad as text (a
link target, a dropped ``file:`` URL or Specto payload path, a file entry, a legacy
card's path) passes ``specto_core.pathguard`` before anything asks whether the file
exists (SPEC-67, SPEC-71).
``markdown_of`` turns dropped entries into markdown: text as text,
pictures as embedded assets, files and prims as links, an item from a Specto view as a
short quote with its source.

Every change (an append or insert, a whole-page write, add / rename / move / remove a
page) is one undo step on the store's own stack, as a canvas submit is on the canvas's:
a step keeps the pages before and after, each touched page's text before and after, and
the assets it added; ``undo`` and ``redo`` put the texts, the pages and the files back (a
removed page's file and assets, and an undone step's assets, wait in ``.trash`` while a
step can bring them back; the trash is emptied when the store opens, as the stack
starts empty each launch). Which page is active is navigation, never a step
(``set_active``). The app's Ctrl+Z takes whichever of the two stacks changed last
(``ScratchStep.ts`` against the canvas record's). A prompt turn's steps fold into one
(``squash``). ``collection(page)`` is a page for the scratch view's ``items`` port: the
page's markdown, then the local files and pictures it links. A store from before pages
became markdown (``index.json`` cards, with or without page folders) is migrated on
open. ``entries_from_mime`` reads a drop or a paste (the drag and drop contract's mime
types, plain bytes per format) into entries. No Qt here.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from .clock import now_iso
from .errors import InvalidArgs, NotFound, TxnError
from .kinds import Item
from .links import Collection
from .locks import is_user_origin
from .pathguard import file_uri_path, path_refusal

SCRATCH_DIR = ".specto/scratch"
PAGES_FILE = "pages.json"
INDEX_FILE = "index.json"  # the card store before #184's markdown pages (migrated)
TRASH_DIR = ".trash"
ASSETS_SUFFIX = ".assets"
VERSION = 2
KINDS = ("text", "image", "file")
MAX_TEXT = 2_000_000  # bytes of one page
MAX_IMAGE = 50_000_000  # bytes of one png
MAX_LABEL = 80
MAX_PAGE_NAME = 40
FIRST_PAGE = "Page 1"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: the drag and drop contract's mime types (``docs/contracts.md`` section 11), in the
#: order a drop target reads them
SPECTO_MIME = "application/x-specto-item+json"
PNG_MIME = "image/png"
URI_MIME = "text/uri-list"
TEXT_MIME = "text/plain"
#: picture kinds of the Specto payload: a png in the drag is their rendering
PICTURE_KINDS = ("image", "frame", "prim", "feature")
#: markdown links and images: ``[text](target)`` / ``![alt](target)``, ``<target>`` too
LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")

ChangeListener = Callable[["ScratchStep | None", str], None]


@dataclass
class NewItem:
    """One dropped thing: ``text`` (markdown), ``png`` (a picture's bytes) or ``path`` (a
    file to link), with an optional label and the view it came from."""

    kind: str
    text: str | None = None
    png: bytes | None = None
    path: str | None = None
    label: str | None = None
    source: dict[str, Any] | None = None


@dataclass
class ScratchStep:
    """One undo step: the pages before and after, the texts it changed (``{page: (before,
    after)}``, ``None`` where the page had no file) and the assets it added
    (``<page>.assets/<name>`` paths relative to the scratch folder)."""

    id: str
    label: str
    origin: str
    ts: str
    pages_before: list[dict[str, str]]
    pages_after: list[dict[str, str]]
    texts: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    assets: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {"step": self.id, "label": self.label, "origin": self.origin, "ts": self.ts}


def _clip(label: str, most: int = MAX_LABEL) -> str:
    label = " ".join(str(label).split())
    return label if len(label) <= most else label[: most - 3] + "..."


def _read_json(path: Path) -> Any:
    """A JSON file's content, ``None`` when it is missing or unreadable (no exception
    escapes: a caught one would keep the caller's frames alive through its traceback)."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _slug(text: str, fallback: str = "image") -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(text or "")).strip("-")[:40]
    return slug or fallback


def _link_label(text: str) -> str:
    """Text safe inside ``[...]`` of a markdown link."""
    return _clip(str(text)).replace("[", "(").replace("]", ")") or "file"


def _target(path: str) -> str:
    """A link target that survives spaces and parentheses: ``<...>`` when needed."""
    return f"<{path}>" if re.search(r"[\s()<>]", path) else path


def joined(text: str, piece: str, at: int | None = None) -> str:
    """``piece`` added to ``text`` as its own block: at the end (``at`` None), or at
    character ``at`` (a caret), with blank lines around it as needed."""
    piece = piece.strip("\n")
    if at is None or at >= len(text):
        if not text.strip():
            return piece + "\n"
        return text.rstrip("\n") + "\n\n" + piece + "\n"
    at = max(0, int(at))
    before, after = text[:at], text[at:]
    lead = (
        "" if not before or before.endswith("\n\n") else ("\n" if before.endswith("\n") else "\n\n")
    )
    tail = "" if after.startswith("\n\n") else ("\n" if after.startswith("\n") else "\n\n")
    return before + lead + piece + tail + after


class ScratchStore:
    """``<root>/.specto/scratch``: the pages, their markdown and assets, and the undo
    stack; see the module docstring. ``clock`` stamps the steps (ISO, as the canvas
    journal does)."""

    def __init__(self, root: Path, *, clock: Callable[[], str] = now_iso) -> None:
        self.root = Path(root)
        self.dir = self.root / SCRATCH_DIR
        self.trash = self.dir / TRASH_DIR
        self.clock = clock
        self.pages: list[dict[str, str]] = []
        self.active = ""
        self._next_page = 1
        self.history: list[ScratchStep] = []
        self.cursor = 0
        self._step_no = 0
        self._listeners: list[ChangeListener] = []
        self._migrate()
        self.load()
        if self.trash.exists():
            shutil.rmtree(self.trash, ignore_errors=True)

    # -- files -------------------------------------------------------------------------
    def md_path(self, page: str) -> Path:
        return self.dir / f"{page}.md"

    def assets_dir(self, page: str) -> Path:
        return self.dir / f"{page}{ASSETS_SUFFIX}"

    def _read(self, page: str) -> str | None:
        path = self.md_path(page)
        if not path.is_file():
            return None
        try:
            return path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")
        except OSError:
            return None

    def _write(self, page: str, text: str | None) -> None:
        """A page's markdown on disk (``None``: its file and assets go to the trash)."""
        path = self.md_path(page)
        if text is None:
            self._move(path, self.trash / path.name)
            self._move(self.assets_dir(page), self.trash / self.assets_dir(page).name)
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        if not path.exists():  # coming back: its assets come back with it
            self._move(self.trash / self.assets_dir(page).name, self.assets_dir(page))
        path.write_bytes(text.encode("utf-8"))

    @staticmethod
    def _move(src: Path, dst: Path) -> None:
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)

    # -- reading -----------------------------------------------------------------------
    def load(self) -> None:
        """Read ``pages.json``; a pad with no pages gets one (``Page 1``, an empty note
        written on the first change)."""
        data = _read_json(self.dir / PAGES_FILE)
        data = data if isinstance(data, dict) else {}
        pages = data.get("pages") if isinstance(data.get("pages"), list) else []
        self.pages = [
            {"id": str(p["id"]), "name": str(p.get("name") or p["id"])}
            for p in pages
            if isinstance(p, dict) and re.match(r"^p\d+$", str(p.get("id", "")))
        ]
        if not self.pages:
            self.pages = [{"id": "p1", "name": FIRST_PAGE}]
        page_nums = [int(p["id"][1:]) for p in self.pages]
        stored = data.get("next_page", 1)
        self._next_page = max(stored if isinstance(stored, int) else 1, max(page_nums) + 1)
        active = data.get("active")
        self.active = active if active in self.page_ids() else self.pages[0]["id"]

    def page_ids(self) -> list[str]:
        return [p["id"] for p in self.pages]

    def page(self, ref: str | None = None) -> dict[str, str]:
        """A page by its id or its name (case-insensitive); the active one for None."""
        if ref is None or str(ref).strip() == "":
            ref = self.active
        ref = str(ref).strip()
        for p in self.pages:
            if p["id"] == ref:
                return p
        for p in self.pages:
            if p["name"].casefold() == ref.casefold():
                return p
        have = ", ".join(f"{p['id']} ({p['name']})" for p in self.pages)
        raise NotFound(f"no scratch page {ref!r}", fix=f"pages: {have}")

    def text(self, page: str | None = None) -> str:
        """A page's markdown (the active one by default; "" before its first change)."""
        return self._read(self.page(page)["id"]) or ""

    def is_empty(self) -> bool:
        return len(self.pages) == 1 and not self.text(self.pages[0]["id"]).strip()

    def links(self, page: str | None = None) -> list[dict[str, Any]]:
        """The local files and pictures a page links or embeds, in order, once each:
        ``{target, path (absolute), image, label}``; web links and missing files left
        out."""
        pid = self.page(page)["id"]
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for m in LINK_RE.finditer(self.text(pid)):
            target = m.group(3).strip("<>")
            path = self.resolve_target(target)
            if path is None or str(path) in seen or not path.is_file():
                continue
            seen.add(str(path))
            out.append(
                {"target": target, "path": path, "image": m.group(1) == "!", "label": m.group(2)}
            )
        return out

    def resolve_target(self, target: str) -> Path | None:
        """A link target of a page as a local path (relative to the scratch folder, or a
        ``file:`` URL); ``None`` for a web link, an anchor, or a path the shared guard
        refuses (``specto_core.pathguard``: a network path ``\\\\host``, ``//host``, also
        percent-encoded, an NT object or device path). Agents write pad text and
        ``links()`` runs on every rebuild, so the text is judged before anything asks
        whether the file exists (SPEC-67)."""
        target = unquote(str(target or "").strip().strip("<>")).split("#")[0]
        if not target:
            return None
        if target.lower().startswith("file:"):
            local = local_path(target)
            return Path(local) if local else None
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) and not re.match(
            r"^[a-z]:[\\/]", target, re.I
        ):
            return None
        if path_refusal(target):
            return None
        p = Path(target)
        return p if p.is_absolute() else (self.dir / p).resolve()

    def collection(self, page: str | None = None) -> Collection:
        """A page for the ``items`` port: the page's markdown (key = page id, part .md),
        then each local file and picture it links (key = its path relative to the
        project, or absolute outside it)."""
        pid = self.page(page)["id"]
        items = [
            Item(
                key=pid,
                parts={".md": str(self.md_path(pid))},
                index=0,
                meta={"kind": "page", "label": self.page(pid)["name"], "page": pid},
            )
        ]
        for i, link in enumerate(self.links(pid), 1):
            path: Path = link["path"]
            rel = self._rel(path)
            role = path.suffix.lower() or ".file"
            meta: dict[str, Any] = {
                "kind": "image" if link["image"] else "file",
                "label": link["label"] or path.name,
                "page": pid,
            }
            if rel is not None:
                meta["rel"] = {role: rel}
            items.append(
                Item(key=rel or path.as_posix(), parts={role: str(path)}, index=i, meta=meta)
            )
        return Collection(items=items, root=str(self.root))

    def _rel(self, path: Path) -> str | None:
        root = self.root.resolve()
        resolved = Path(path).resolve()
        if resolved != root and root not in resolved.parents:
            return None
        return resolved.relative_to(root).as_posix()

    # -- listeners -------------------------------------------------------------------
    def on_change(self, listener: ChangeListener) -> Callable[[], None]:
        """``listener(step, kind)`` after every change (``kind``: text, page, undo, redo,
        squash; ``active`` when the active page changed, with no step). Returns the
        unsubscribe."""
        self._listeners.append(listener)

        def off() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return off

    def _notify(self, step: ScratchStep | None, kind: str) -> None:
        for cb in list(self._listeners):
            cb(step, kind)

    # -- navigation --------------------------------------------------------------------
    def set_active(self, ref: str) -> bool:
        """Show a page (by id or name): navigation, no undo step. False when it was
        already the active one."""
        pid = self.page(ref)["id"]
        if pid == self.active:
            return False
        self.active = pid
        self._save_pages()
        self._notify(None, "active")
        return True

    # -- markdown from drops -------------------------------------------------------------
    def link_to(self, path: str | Path) -> str:
        """A link target for a file, from the scratch folder: relative inside the
        project, a ``file:///`` URL outside it. ``InvalidArgs`` for a path the shared guard
        refuses (a network, NT object or device path), before anything resolves it
        (SPEC-71). Callers pass the text they were given, not a path joined onto the
        project root, so a project on a network share still links its own files."""
        why = path_refusal(str(path))
        if why:
            raise InvalidArgs(f"{path} is {why}", fix="link a file on this machine")
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        if self._rel(p) is None:
            return p.resolve().as_uri()
        return os.path.relpath(p.resolve(), self.dir.resolve()).replace("\\", "/")

    def markdown_of(
        self, entries: list[NewItem], page: str | None = None
    ) -> tuple[str, list[tuple[str, bytes]]]:
        """Dropped entries as markdown for ``page``, and the pictures to store with it
        (``(asset name, png)``): text as text; a picture as an embedded asset; a file
        as a link; anything that came from a Specto view as a short quote naming its
        source."""
        pid = self.page(page)["id"]
        taken = (
            {p.name for p in self.assets_dir(pid).glob("*")}
            if self.assets_dir(pid).is_dir()
            else set()
        )
        blocks: list[str] = []
        assets: list[tuple[str, bytes]] = []
        for e in entries:
            src = e.source or {}
            origin = " · ".join(
                str(x) for x in (src.get("view"), src.get("path") or src.get("key")) if x
            )
            if e.kind == "text":
                text = (e.text or "").replace("\r\n", "\n").strip("\n")
                if not text.strip():
                    continue
                if origin:
                    quoted = "\n".join(("> " + line).rstrip() for line in text.split("\n"))
                    blocks.append(f"{quoted}\n>\n> — {origin}")
                else:
                    blocks.append(text)
            elif e.kind == "image":
                data = e.png or b""
                if not data.startswith(PNG_MAGIC):
                    raise InvalidArgs("the image is not a png", fix="convert it to png first")
                if len(data) > MAX_IMAGE:
                    raise InvalidArgs(f"the image is {len(data)} bytes, more than {MAX_IMAGE}")
                label = _link_label(e.label or src.get("label") or src.get("key") or "image")
                stem = _slug(label)
                name, n = f"{stem}.png", 2
                while name in taken:
                    name, n = f"{stem}-{n}.png", n + 1
                taken.add(name)
                assets.append((name, data))
                line = f"![{label}]({_target(f'{pid}{ASSETS_SUFFIX}/{name}')})"
                blocks.append(line + (f"\n*{label} — {origin}*" if origin else ""))
            elif e.kind == "file":
                if not e.path:
                    continue
                why = path_refusal(e.path)
                if why:  # before anything asks whether it exists (SPEC-71)
                    raise InvalidArgs(f"{e.path} is {why}", fix="link a file on this machine")
                p = Path(e.path)
                if not p.is_absolute():
                    p = self.root / p
                if not p.exists():
                    raise NotFound(f"no file {e.path}", fix="link a file that exists")
                label = _link_label(e.label or p.name)
                link = f"[{label}]({_target(self.link_to(e.path))})"
                blocks.append(f"> {link}\n>\n> — {origin}" if origin else link)
            else:
                raise InvalidArgs(f"unknown kind {e.kind!r}", fix="text, image or file")
        return "\n\n".join(blocks), assets

    # -- changes -------------------------------------------------------------------------
    def append(
        self,
        markdown: str,
        *,
        page: str | None = None,
        at: int | None = None,
        assets: list[tuple[str, bytes]] = (),  # type: ignore[assignment]
        origin: str = "user",
        label: str | None = None,
    ) -> ScratchStep:
        """Add ``markdown`` to a page (the active one by default) as its own block: at
        the end, or at character ``at`` (the caret of an editor); ``assets`` are the
        pictures it embeds, stored in the page's assets folder. One step."""
        pid = self.page(page)["id"]
        piece = str(markdown or "").replace("\r\n", "\n")
        if not piece.strip() and not assets:
            raise InvalidArgs("nothing to add", fix="give the markdown to add")
        old = self._read(pid)
        new = joined(old or "", piece, at)
        self._check(new)
        added = []
        folder = self.assets_dir(pid)
        for name, data in assets:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(data)
            added.append(f"{pid}{ASSETS_SUFFIX}/{name}")
        if label is None:
            first = next((ln.strip("#>*-! ") for ln in piece.splitlines() if ln.strip()), "")
            label = f"add {_clip(first, 40)}" if first else "add to the page"
        return self._commit(self.pages, self.pages, {pid: (old, new)}, label, origin, "text", added)

    def write(
        self,
        markdown: str,
        *,
        page: str | None = None,
        origin: str = "user",
        label: str | None = None,
    ) -> ScratchStep | None:
        """Replace a page's whole markdown (the editor's autosave, the agent's
        scratch_write). One step; ``None`` when nothing changed."""
        pid = self.page(page)["id"]
        new = str(markdown or "").replace("\r\n", "\n")
        old = self._read(pid)
        if (old or "") == new:
            return None
        self._check(new)
        name = self.page(pid)["name"]
        label = label or f"edit page {name}"
        return self._commit(self.pages, self.pages, {pid: (old, new)}, label, origin, "text")

    @staticmethod
    def _check(text: str) -> None:
        if len(text.encode("utf-8")) > MAX_TEXT:
            raise InvalidArgs(f"the page would be more than {MAX_TEXT} bytes")

    def _page_name(self, name: Any, *, keep: str | None = None) -> str:
        name = _clip(str(name or ""), MAX_PAGE_NAME)
        if not name:
            raise InvalidArgs("the page needs a name", fix="a few words: lidar, exposure")
        taken = {p["name"].casefold() for p in self.pages if p["id"] != keep}
        if name.casefold() in taken:
            raise InvalidArgs(f"a page is called {name!r} already", fix="pick another name")
        return name

    def add_page(
        self, name: str | None = None, *, at: int | None = None, origin: str = "user"
    ) -> ScratchStep:
        """A new empty page (``Page <n>`` when unnamed) at ``at`` (the end), one step;
        it becomes the active page."""
        pid = f"p{self._next_page}"
        if name is None or not str(name).strip():
            n = len(self.pages) + 1
            while f"page {n}" in {p["name"].casefold() for p in self.pages}:
                n += 1
            name = f"Page {n}"
        name = self._page_name(name)
        after = copy.deepcopy(self.pages)
        pos = len(after) if at is None or at < 0 else min(int(at), len(after))
        after.insert(pos, {"id": pid, "name": name})
        self._next_page += 1
        self.active = pid
        return self._commit(
            self.pages, after, {pid: (None, "")}, f"add page {name}", origin, "page"
        )

    def rename_page(self, ref: str, name: str, *, origin: str = "user") -> ScratchStep | None:
        page = self.page(ref)
        name = self._page_name(name, keep=page["id"])
        if name == page["name"]:
            return None
        after = copy.deepcopy(self.pages)
        next(p for p in after if p["id"] == page["id"])["name"] = name
        label = f"rename page {page['name']} to {name}"
        return self._commit(self.pages, after, {}, label, origin, "page")

    def move_page(self, ref: str, index: int, *, origin: str = "user") -> ScratchStep | None:
        page = self.page(ref)
        ids = [p for p in self.page_ids() if p != page["id"]]
        index = max(0, min(int(index), len(ids)))
        ids.insert(index, page["id"])
        if ids == self.page_ids():
            return None
        by_id = {p["id"]: p for p in copy.deepcopy(self.pages)}
        after = [by_id[i] for i in ids]
        return self._commit(self.pages, after, {}, f"move page {page['name']}", origin, "page")

    def remove_page(self, ref: str, *, origin: str = "user") -> ScratchStep:
        """Close a page (its file and assets wait in the trash for an undo), one step;
        the pad keeps at least one page."""
        page = self.page(ref)
        if len(self.pages) == 1:
            raise InvalidArgs(
                "the scratch pad keeps at least one page", fix="clear its text instead"
            )
        after = [p for p in copy.deepcopy(self.pages) if p["id"] != page["id"]]
        old = self._read(page["id"])
        words = " (not empty)" if (old or "").strip() else ""
        return self._commit(
            self.pages,
            after,
            {page["id"]: (old, None)},
            f"close page {page['name']}{words}",
            origin,
            "page",
        )

    # -- steps -------------------------------------------------------------------------
    def _commit(
        self,
        pages_before: list[dict[str, str]],
        pages_after: list[dict[str, str]],
        texts: dict[str, tuple[str | None, str | None]],
        label: str,
        origin: str,
        kind: str,
        assets: list[str] | None = None,
    ) -> ScratchStep:
        self._step_no += 1
        step = ScratchStep(
            id=f"x{self._step_no}",
            label=label,
            origin=origin,
            ts=self.clock(),
            pages_before=copy.deepcopy(pages_before),
            pages_after=copy.deepcopy(pages_after),
            texts=dict(texts),
            assets=list(assets or []),
        )
        del self.history[self.cursor :]
        self.history.append(step)
        self.cursor = len(self.history)
        self._apply(step.pages_after, {k: v[1] for k, v in texts.items()})
        self._notify(step, kind)
        return step

    @property
    def top(self) -> ScratchStep | None:
        """What Undo would revert."""
        return self.history[self.cursor - 1] if self.cursor else None

    @property
    def next_redo(self) -> ScratchStep | None:
        return self.history[self.cursor] if self.cursor < len(self.history) else None

    def undo(self) -> ScratchStep:
        step = self.top
        if step is None:
            raise TxnError("nothing to undo on the scratch pad")
        for rel in step.assets:
            self._move(self.dir / rel, self.trash / rel)
        self._apply(step.pages_before, {k: v[0] for k, v in step.texts.items()})
        self.cursor -= 1
        self._notify(step, "undo")
        return step

    def redo(self) -> ScratchStep:
        step = self.next_redo
        if step is None:
            raise TxnError("nothing to redo on the scratch pad")
        for rel in step.assets:
            self._move(self.trash / rel, self.dir / rel)
        self._apply(step.pages_after, {k: v[1] for k, v in step.texts.items()})
        self.cursor += 1
        self._notify(step, "redo")
        return step

    def squash(self, start: int, *, label: str, origin: str) -> ScratchStep | None:
        """Fold the steps above ``start`` (one prompt turn's) into one; the pad does not
        change. Steps the user made in that range stay their own: only the ones above
        the last user step fold. ``None`` when there is none to fold."""
        if start > self.cursor or self.cursor != len(self.history):
            return None
        for i in range(start, self.cursor):
            if is_user_origin(self.history[i].origin):
                start = i + 1
        steps = self.history[start:]
        if not steps:
            return None
        if len(steps) == 1:
            return steps[0]
        texts: dict[str, tuple[str | None, str | None]] = {}
        assets: list[str] = []
        for s in steps:
            for k, (b, a) in s.texts.items():
                texts[k] = (texts[k][0] if k in texts else b, a)
            assets.extend(a for a in s.assets if a not in assets)
        self._step_no += 1
        step = ScratchStep(
            id=f"x{self._step_no}",
            label=label,
            origin=origin,
            ts=self.clock(),
            pages_before=steps[0].pages_before,
            pages_after=steps[-1].pages_after,
            texts=texts,
            assets=assets,
        )
        del self.history[start:]
        self.history.append(step)
        self.cursor = len(self.history)
        self._notify(step, "squash")
        return step

    def _apply(self, pages: list[dict[str, str]], texts: dict[str, str | None]) -> None:
        """Make ``pages`` the pad's and write ``texts`` (``None``: the page's file goes);
        when the shown page went, its neighbour shows."""
        at = self.page_ids().index(self.active) if self.active in self.page_ids() else 0
        for pid, text in texts.items():
            self._write(pid, text)
        self.pages = copy.deepcopy(pages)
        if self.active not in self.page_ids():
            self.active = self.pages[min(at, len(self.pages) - 1)]["id"]
        self._save_pages()

    def _save_pages(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        data = {
            "version": VERSION,
            "next_page": self._next_page,
            "active": self.active,
            "pages": self.pages,
        }
        _write_json(self.dir / PAGES_FILE, data)

    # -- what the agent reads ----------------------------------------------------------
    def page_summary(self, page: Mapping[str, str]) -> dict[str, Any]:
        text = self._read(page["id"]) or ""
        return {
            "id": page["id"],
            "name": page["name"],
            "path": f"{SCRATCH_DIR}/{page['id']}.md",
            "chars": len(text),
            "active": page["id"] == self.active,
        }

    # -- migration from the card store ---------------------------------------------------------
    def _migrate(self) -> None:
        """Cards (``index.json``: texts, pictures, file references) as page markdown:
        the store before pages (one ``index.json`` in the scratch folder, page p1) and
        the one with page folders (``<page>/index.json``). Each card becomes a block in
        order; a picture moves into the page's assets."""
        if not self.dir.is_dir():
            return
        data = _read_json(self.dir / PAGES_FILE)
        if isinstance(data, dict) and data.get("version") == VERSION:
            return
        cards: dict[str, Path] = {}
        if (self.dir / INDEX_FILE).is_file() and not isinstance(data, dict):
            cards["p1"] = self.dir
            data = {"pages": [{"id": "p1", "name": FIRST_PAGE}], "active": "p1"}
        elif isinstance(data, dict):
            for p in data.get("pages") or []:
                pid = str(p.get("id", "")) if isinstance(p, dict) else ""
                folder = self.dir / pid
                if not re.match(r"^p\d+$", pid) or _linked(folder):
                    continue  # a page folder that links out is never read (SPEC-71)
                if (folder / INDEX_FILE).is_file():
                    cards[pid] = folder
        else:
            return
        for pid, folder in cards.items():
            index = _read_json(folder / INDEX_FILE)
            items = index.get("items") if isinstance(index, dict) else None
            blocks: list[str] = []
            for it in items if isinstance(items, list) else []:
                if isinstance(it, dict):
                    block = self._card_markdown(pid, folder, it)
                    if block:
                        blocks.append(block)
            old = self._read(pid)
            text = "\n\n".join(blocks) + ("\n" if blocks else "")
            self.md_path(pid).write_bytes(
                joined(old or "", text).encode("utf-8") if old else text.encode("utf-8")
            )
            (folder / INDEX_FILE).unlink()
            if folder != self.dir:
                with contextlib.suppress(OSError):
                    folder.rmdir()  # only when nothing else is left in it
        data["version"] = VERSION
        self.dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.dir / PAGES_FILE, data)

    def _card_file(self, pid: str, folder: Path, name: str) -> Path | None:
        """A text or image card's ``file`` from the agent-writable ``index.json`` as a
        file directly in the card folder, or None. The name is judged by its text first
        (a network, device or stream path is never asked about), then must be one plain
        name, then a regular file (not a link) whose resolved parent is the page's folder
        as the store places it (the scratch folder, or ``<scratch>/<pid>``), never the
        folder's own resolve, which a junction would send elsewhere: nothing outside it is
        read, moved or deleted (SPEC-71)."""
        if path_refusal(name) or name in ("", ".", "..") or re.search(r"[\\/]", name):
            return None
        path = folder / name
        if path.is_symlink() or not path.is_file():
            return None
        home = self.dir.resolve() if folder == self.dir else self.dir.resolve() / pid
        return path if path.resolve().parent == home else None

    def _card_markdown(self, pid: str, folder: Path, it: dict[str, Any]) -> str:
        kind, label = it.get("kind"), str(it.get("label") or "")
        if kind == "text" and it.get("file"):
            path = self._card_file(pid, folder, str(it["file"]))
            if path is None:
                return ""
            text = path.read_text(encoding="utf-8", errors="replace")
            with contextlib.suppress(OSError):
                path.unlink()
            return text.strip("\n")
        if kind == "image" and it.get("file"):
            name = str(it["file"])
            if _linked(self.assets_dir(pid)):
                return ""  # never moved through a link or junction out (SPEC-71)
            src = self._card_file(pid, folder, name)
            if src is None:
                return ""
            dst = self.assets_dir(pid) / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
            alt = _link_label(label or "image")
            return f"![{alt}]({_target(f'{pid}{ASSETS_SUFFIX}/{name}')})"
        if kind == "file" and it.get("path"):
            text = str(it["path"])
            if path_refusal(text):
                # a network or device path from the agent-writable index.json: named as
                # plain text, never resolved or linked (SPEC-71)
                return _link_label(label or text)
            name = Path(text).name
            return f"[{_link_label(label or name)}]({_target(self.link_to(text))})"
        return ""


def _linked(folder: Path) -> bool:
    """A symlink or (Windows) junction: ``lstat`` only, its target is never opened."""
    return folder.is_symlink() or folder.is_junction()


# -- drops and pastes ------------------------------------------------------------------------
def _decode(data: bytes | None) -> str:
    if not data:
        return ""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(data) > 1 and data[1:2] == b"\x00"):
        with contextlib.suppress(UnicodeDecodeError):
            return data.decode("utf-16").rstrip("\x00")
    return data.decode("utf-8", errors="replace").rstrip("\x00")


def local_path(url: str) -> str | None:
    """A ``file:`` URL's local path (``file:///C:/a%20b.png`` -> ``C:/a b.png``); ``None``
    for another scheme, for a URL naming a host (``file://server/share/x``: a network
    path, never built as a UNC share, SPEC-67) and for a path the shared guard refuses
    (``specto_core.pathguard.file_uri_path``)."""
    url = url.strip()
    if urlparse(url).scheme != "file":
        return None
    path, why = file_uri_path(url)
    return None if why else path


def entries_from_mime(formats: Mapping[str, bytes]) -> list[NewItem]:
    """What a drop or a paste adds, read in the contract's order: Specto's own payload,
    then a png, then file URLs, then text. ``formats`` maps a mime type to its bytes
    (another image type than png comes as ``image/<x>``; the caller converts it).

    - Specto's payload: a text selection (items of kind ``text``) is a text of the words
      (the item's ``text``, else the payload's), whatever file it came from; items with a
      file (``abs``) are file references, unless the drag is a picture (a crop, a frame,
      a snapshot: a png beside a picture kind, or ``meta.rendered``), which is one image;
      items with neither are texts of their label; a payload with only ``text`` is one
      text. None of this needs ``text/uri-list``.
    - a png (or another ``image/*``) alone: one image.
    - ``text/uri-list``: each local file a reference; a web address a text with the link.
    - ``text/plain``: one text.
    """
    png = formats.get(PNG_MIME)
    other_image = next(
        (formats[k] for k in formats if k.startswith("image/") and k != PNG_MIME and formats[k]),
        None,
    )
    raw = formats.get(SPECTO_MIME)
    if raw:
        try:
            payload = json.loads(_decode(raw))
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            out = _from_payload(payload, png)
            if out:
                return out
    if png or other_image:
        return [NewItem(kind="image", png=png or other_image)]
    uris = _decode(formats.get(URI_MIME))
    if uris.strip():
        out = []
        for line in uris.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            path = local_path(line)
            if path is not None:
                if Path(path).exists():
                    out.append(NewItem(kind="file", path=path))
            elif re.match(r"^https?://", line):
                name = PurePosixPath(urlparse(line).path).name or line
                out.append(NewItem(kind="text", text=f"[{name}]({line})", label=_clip(name)))
        if out:
            return out
    text = _decode(formats.get(TEXT_MIME))
    if text.strip():
        return [NewItem(kind="text", text=text)]
    return []


def _from_payload(payload: dict[str, Any], png: bytes | None) -> list[NewItem]:
    items = [it for it in (payload.get("items") or []) if isinstance(it, dict)]
    text = payload.get("text")
    if not items:
        return (
            [NewItem(kind="text", text=str(text))] if isinstance(text, str) and text.strip() else []
        )

    def source(it: dict[str, Any]) -> dict[str, Any]:
        src = {k: it.get(k) for k in ("view", "key", "path") if it.get(k) is not None}
        if it.get("label"):
            src["label"] = it["label"]
        return src

    # a text selection (markdown, json, html views) is the words, not the file they came
    # from: the item's own text, else the payload's
    if all(it.get("kind") == "text" for it in items):
        out = []
        for it in items:
            words = it.get("text") if isinstance(it.get("text"), str) else None
            words = words if words and words.strip() else text
            if isinstance(words, str) and words.strip():
                out.append(
                    NewItem(kind="text", text=words, label=it.get("label"), source=source(it))
                )
        if out:
            return out
    # an item's path is text from the drag source: judged before anything asks whether
    # it exists (SPEC-71)
    files = [
        it
        for it in items
        if it.get("abs") and not path_refusal(str(it["abs"])) and Path(str(it["abs"])).exists()
    ]
    picture = png is not None and (
        len(files) < len(items)
        or any(it.get("kind") in PICTURE_KINDS[1:] for it in items)
        or any((it.get("meta") or {}).get("rendered") for it in items)
    )
    if picture:
        first = items[0]
        return [NewItem(kind="image", png=png, label=first.get("label"), source=source(first))]
    out: list[NewItem] = []
    for it in items:
        if it in files:
            out.append(
                NewItem(kind="file", path=str(it["abs"]), label=it.get("label"), source=source(it))
            )
        elif isinstance(it.get("text"), str) and it["text"].strip():
            out.append(
                NewItem(kind="text", text=it["text"], label=it.get("label"), source=source(it))
            )
        else:
            words = it.get("label") or it.get("key") or it.get("path")
            if words:
                out.append(NewItem(kind="text", text=str(words), source=source(it)))
    if not out and isinstance(text, str) and text.strip():
        out.append(NewItem(kind="text", text=text))
    return out


__all__ = [
    "ASSETS_SUFFIX",
    "FIRST_PAGE",
    "KINDS",
    "PAGES_FILE",
    "PNG_MIME",
    "SCRATCH_DIR",
    "SPECTO_MIME",
    "TEXT_MIME",
    "URI_MIME",
    "NewItem",
    "ScratchStep",
    "ScratchStore",
    "entries_from_mime",
    "joined",
    "local_path",
]
