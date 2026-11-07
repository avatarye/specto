"""The running canvas (ADRs 0012, 0013): the project's one canvas document, live, with
submit, undo and the journal.

An agent (or a human gesture) hands in a whole canvas document, or edits of the live one
(#98), through ``submit``. It is validated (every problem at once, ``CanvasInvalid``),
diffed against the live document (``make_patch``), and the difference is applied as one
undo step and one journal entry. Undo applies the step's inverse patch. View hosts react
to the patch through ``on_patch``; nothing here reaches into views.

    live = LiveCanvas.open(project)            # .specto/canvas.json + canvas.journal.jsonl
    live = LiveCanvas.headless("demo")          # in memory, for tests and the SDK fakes
    res = live.submit(document, origin="agent:s1", label="added a rear gallery")
    res = live.submit(edits={"views": {"kobi": {"label": "rear"}}}, origin="agent:s1")
    live.undo(); live.redo()
"""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from pydantic import Field

from .canvas_document import (
    TREE_WATCH,
    CanvasInvalid,
    Problem,
    fit_project_source,
    infer_intaker_scopes,
    new_canvas_document,
    parse_canvas_text,
    reach_problems,
    validate_canvas,
    watch_the_file_tree,
)
from .diff_text import patch_label
from .edits import (
    MISSING,
    already_removed,
    edit_conflict,
    lost_ends,
    merge_edits,
    three_way,
)
from .errors import PatchError, TxnError
from .events import EventStream
from .journal import Journal, JournalEntry
from .locks import is_user_origin, lock_problems
from .names import rename_legacy_ids, rename_placeholders
from .patch import apply_patch, invert_patch, make_patch
from .project import Project
from .quotas import Quotas, ViewCostFn, quota_problems
from .registry import Result
from .txn import (
    OBJECT_PATH,
    TxnRecord,
    UndoResult,
    rebuild_history,
    stack_applies,
    touched_objects,
)
from .unknown_files import file_problems

#: The one canvas's id in records and addresses (ADR 0012: there is only one).
CANVAS_ID = "canvas"

#: ``listener(patch, meta)``; ``meta = {kind: submit|undo|redo|load|navigate, txn, origin,
#: label, touched: [view and link ids]}``. On ``load`` the patch is empty: rebuild from
#: ``data``; ``navigate`` moved the window frame (no undo step).
PatchListener = Callable[[list[dict[str, Any]], dict[str, Any]], None]


class SubmitResult(Result):
    """What a submit changed. ``empty`` when the document equalled the live one (no undo
    step, nothing journaled)."""

    txn: str | None = None
    label: str = ""
    origin: str = "user"
    empty: bool = False
    patch: list[dict[str, Any]] = Field(default_factory=list)
    #: view and link ids the patch touched, in patch order (what the shell outlines)
    touched: list[str] = Field(default_factory=list)
    views: dict[str, list[str]] = Field(default_factory=dict, description="added/removed/changed")
    links: dict[str, list[str]] = Field(default_factory=dict, description="added/removed/changed")
    renamed: dict[str, str] = Field(
        default_factory=dict,
        description="New views and links submitted under a placeholder key, placeholder -> "
        "the name (views) or l<n> id (links) the app gave them (call them by that from now on).",
    )
    already_removed: list[str] = Field(
        default_factory=list,
        description="Views and links the edits removed that were already gone (the user "
        "removed them meanwhile): nothing was left to do for them.",
    )
    cursor: int = 0
    depth: int = 0


class CanvasConflict(CanvasInvalid):
    """A submit made during a prompt turn would change an object the user changed after
    the turn began (#98): refused with the object's live entry. The agent did nothing
    wrong, so the prompt turn does not count it as a rejected submit."""

    code = "conflict"


#: how many turn-start copies ``mark`` keeps (the prompt bar needs the newest)
MARK_COPIES = 4
#: what a submit off the canvas's own thread does: ``warn`` (the app's default), ``raise``
#: (the test suite sets it, conftest.py) or ``off``
THREAD_CHECK_ENV = "SPECTO_THREAD_CHECK"


def _conflict(section: str, oid: str, entry: Any, fields: list[str], *, edit: bool) -> Problem:
    """A turn-start conflict on one object: removed by the user, or changed in
    ``fields`` (dotted paths; ``""`` for the object as a whole) by both sides. ``since``
    stays the turn's start, so writing over the user's value is refused again: the fix-it
    says how to keep it, per submit form. An edit leaves the field out; a whole document
    copies the live value (leaving it out of a document would remove it, refused again)."""
    if entry is MISSING:
        return Problem(
            path=f"/{section}/{oid}",
            message=f"{oid} was removed by the user during this turn",
            fix=f"leave {oid} out; add it again only if the user asks for it",
        )
    which = ", ".join(f for f in fields if f)
    what = f"{oid}'s {which}" if which else oid
    how = (
        "leave it out of your edits"
        if edit
        else "copy its live value (from this entry) into your document"
    )
    return Problem(
        path=f"/{section}/{oid}",
        message=f"{oid} was changed by the user during this turn"
        + (f" ({which})" if which else "")
        + f"; its entry is now {json.dumps(entry, separators=(',', ':'))}",
        fix=f"keep the user's {what}: {how}, or ask the user before changing it",
    )


def _link_conflict(lid: str, views: list[str]) -> Problem:
    """A link the user added or changed this turn that the submit would leave without one
    of its views."""
    names = " and ".join(views)
    return Problem(
        path=f"/links/{lid}",
        message=f"{lid} was added or changed by the user during this turn and links {names}, "
        "which your change removes",
        fix=f"keep {names}, or ask the user before removing it",
    )


class SerialLock:
    """The canvas's one serialise lock (SPEC-18): re-entrant, and it knows its holder. Every
    endpoint that calls the canvas from threads of its own takes it around each request
    (the SDK's test endpoints, the app's endpoint); a submit on such a thread passes the
    thread check only while holding it."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._holder: int | None = None
        self._depth = 0

    def __enter__(self) -> SerialLock:
        self._lock.acquire()
        self._holder = threading.get_ident()
        self._depth += 1
        return self

    def __exit__(self, *exc: object) -> None:
        self._depth -= 1
        if not self._depth:
            self._holder = None
        self._lock.release()

    def held(self) -> bool:
        """Whether the calling thread holds it."""
        return self._holder == threading.get_ident()


def _changes(patch: list[dict[str, Any]], section: str) -> dict[str, list[str]]:
    """Ids under ``/<section>/`` a patch adds, removes or changes (a field inside)."""
    out: dict[str, list[str]] = {"added": [], "removed": [], "changed": []}
    prefix = f"/{section}/"
    for op in patch:
        path = op["path"]
        if not path.startswith(prefix):
            continue
        oid, _, rest = path[len(prefix) :].partition("/")
        if rest or op["op"] == "replace":
            bucket = "changed"
        else:
            bucket = "added" if op["op"] == "add" else "removed"
        if oid not in out[bucket]:
            out[bucket].append(oid)
    return out


class LiveCanvas:
    def __init__(
        self,
        data: dict[str, Any],
        *,
        project: Project | None = None,
        journal: bool = True,
        autosave: bool = True,
        clock: Callable[[], float] = time.monotonic,
        quotas: Quotas | None = None,
        view_cost: ViewCostFn | None = None,
    ) -> None:
        #: the live document in its normalised JSON form (what ``to_data`` writes), so a
        #: resubmitted equal document diffs to nothing
        #: the load made the canvas fit the project source (#83): links moved to
        #: ``project.all_files`` (in order) and how far the layout moved right off the
        #: project node; ``open`` journals both once (not an undo step)
        fit = fit_project_source(data)
        self.rewired: list[str] = fit.links
        self.shifted: float = fit.shift
        infer_intaker_scopes(data)  # loading is tolerant of scope-less intakers
        #: intakers the load turned on for the file tree (#115): one that did not say,
        #: and once per project (``TREE_WATCH``) one saved off as the old default
        saved_before = project is not None and not project.rewrote(TREE_WATCH)
        self.watched: list[str] = watch_the_file_tree(data, saved_before=saved_before)
        #: legacy ``v<n>`` ids renamed to names on load (ADR 0018), old -> new; ``open``
        #: journals the rename once (not an undo step)
        self.renamed: dict[str, str] = rename_legacy_ids(data)
        self.data = validate_canvas(data).to_data()
        self.project = project
        self.autosave = autosave and project is not None
        self.clock = clock
        #: quotas judged at submit; ``view_cost`` is what one live view costs (the engine's:
        #: the plugin registry's cost per type, with params and machine applied)
        #: (None: every view is free, as in headless tests without an engine)
        self.quotas = quotas or Quotas()
        self.view_cost: ViewCostFn = view_cost or (lambda _view: None)
        jpath = project.journal_file if (project is not None and journal) else None
        self.journal = Journal(jpath)
        self.journal.on_write_error = lambda e: self._disk_denied(e, jpath, "the journal")
        self.events = EventStream()
        #: the disk error of the last save, None once a save lands (``save``)
        self.save_error: OSError | None = None
        #: the undo stack and the cursor (records not undone); ``log`` is commit order
        self.history: list[TxnRecord] = []
        self.log: list[TxnRecord] = []
        self.cursor = 0
        self.dirty = False
        #: why the undo stack was not rebuilt from the journal (None: it was, or nothing)
        self.history_note: str | None = None
        self._txn_no = 0
        self._listeners: list[PatchListener] = []
        #: every change of the document this session, ``(origin, touched)`` in order
        #: (submits, undos, redos; not navigation): what ``mark`` counts and the
        #: conflict rule reads
        self._changes: list[tuple[str, list[str]]] = []
        #: each recent ``mark``: its place in ``_changes`` and the views and links as it
        #: found them (the turn-start copy a submit with that ``since`` merges against,
        #: SPEC-18); the newest ``MARK_COPIES`` are kept
        self._marks: dict[int, tuple[int, dict[str, dict[str, Any]]]] = {}
        self._mark_no = 0
        #: the thread that owns the canvas (the GUI thread in the app, where ``SpectoApp``
        #: opens it): every submit runs there, or on a thread holding ``serial_lock``, so
        #: one submit's read, merge and apply never interleave with another's
        self.owner_thread = threading.get_ident()
        self.serial_lock = SerialLock()

    # -- construction ------------------------------------------------------------------
    @classmethod
    def headless(cls, label: str = "", **kw: Any) -> LiveCanvas:
        """A fresh canvas in memory (the file tree fed from the project folder)."""
        return cls(new_canvas_document(label), **kw)

    @classmethod
    def open(cls, project: Project, **kw: Any) -> LiveCanvas:
        """The project's canvas with its undo history restored from the journal."""
        data, renamed = project.load_canvas(rewire=False)
        live = cls(data, project=project, **kw)
        live.renamed = renamed
        live._restore_history()
        if renamed or live.rewired or live.shifted or live.watched:
            live._journal_rewrite(project)
        project.mark_rewritten(TREE_WATCH)
        live._notify([], {"kind": "load", "txn": None, "origin": "user", "label": ""})
        return live

    def _journal_rewrite(self, project: Project) -> None:
        """The saved canvas was rewritten on load (legacy ids renamed, #79; links moved to
        the project source, #83): one journal entry carrying the whole difference (so a
        replay lands on it too), the undo history dropped (its patches name the old
        ids and ports), the rewritten document saved."""
        before, _ = project.load_canvas(rename=False, rewire=False)
        patch = make_patch(before, self.data)
        self.history, self.log, self.cursor = [], [], 0
        n, m = len(self.renamed), len(self.rewired)
        parts = [f"renamed {n} view{'s' if n != 1 else ''} to names"] if n else []
        if m:
            parts.append(f"moved {m} link{'s' if m != 1 else ''} to the project source")
        if self.shifted:
            parts.append(f"moved the layout {self.shifted:g} px right of the project node")
        if self.watched:
            parts.append("turned auto-update on for the file tree")
        label = "; ".join(parts)
        # ``rename`` alone is #79's entry; anything that fits the project source (#83,
        # and the file tree's name since #87) is one ``rewire`` entry
        kind = "rewire" if (m or self.shifted or self.watched) else "rename"
        self.history_note = (
            "undo history not restored: the views were renamed to names (#79)"
            if kind == "rename"
            else "undo history not restored: the canvas was fitted to the project source (#83)"
        )
        entry = JournalEntry(
            seq=self.journal.next_seq(),
            kind=kind,  # type: ignore[arg-type]
            txn="",
            txn_no=self._txn_no,
            origin="user",
            label=label,
            patch=patch,
            inverse=invert_patch(before, patch),
            touched=touched_objects(patch),
            renamed=dict(self.renamed),
            rewired=list(self.rewired),
        )
        self.journal.append(entry)
        self.dirty = True
        if self.autosave:
            self.save()

    def _restore_history(self) -> None:
        """Rebuild the undo stack from the journal as it was written (ADR 0013: undo
        history is the document's history). The stack must agree with the saved
        document (its top patches apply); otherwise it starts empty and
        ``history_note`` says why."""
        entries = self.journal.read() if self.journal.path is not None else []
        history: list[TxnRecord] = []
        log: list[TxnRecord] = []
        cursor = 0
        note: str | None = None
        if entries:
            try:
                history, log, cursor = rebuild_history(CANVAS_ID, entries)
            except ValueError as e:
                note = f"undo history not restored: {e}"
            if note is None and not stack_applies(self.data, history, cursor):
                note = "undo history not restored: the journal does not match the saved document"
        if note is not None:
            history, log, cursor = [], [], 0
        self.history, self.log, self.cursor = history, log, cursor
        self.history_note = note
        self._txn_no = max((e.txn_no for e in entries), default=0)
        self.events.emit(
            "history.restored", {"depth": len(history), "cursor": cursor, "note": note}
        )

    # -- reading -----------------------------------------------------------------------------
    def document(self) -> dict[str, Any]:
        """A deep copy of the live canvas document (its JSON form)."""
        return copy.deepcopy(self.data)

    def undo_info(self, *, last: int = 5) -> dict[str, Any]:
        """Cursor, depth and the newest records (what ``state`` shows)."""
        return {
            "cursor": self.cursor,
            "depth": len(self.history),
            "can_undo": self.cursor > 0,
            "can_redo": self.cursor < len(self.history),
            "history": self.history_summaries(last=last),
            "note": self.history_note,
        }

    def history_summaries(
        self, *, last: int | None = None, with_patch: bool = False
    ) -> list[dict[str, Any]]:
        """The submits in commit order; ``with_patch`` adds each one's patch, inverse
        and the diff as text (spec #35 story 15: the detail only on request)."""
        undone = {r.id for r in self.history[self.cursor :]}
        recs = self.log[-last:] if last else self.log
        out = []
        for r in recs:
            entry = {**r.summary(), "undone": r.id in undone}
            if with_patch:
                from .diff_text import describe_patch

                entry.update(
                    patch=r.patch, inverse=r.inverse, diff=describe_patch(r.patch, r.inverse)
                )
            out.append(entry)
        return out

    @property
    def top_id(self) -> str | None:
        """The id of the newest submit not undone (what Undo would revert), or None."""
        return self.history[self.cursor - 1].id if self.cursor else None

    def record(self, txn_id: str) -> TxnRecord:
        for r in self.log:
            if r.id == txn_id:
                return r
        raise TxnError(f"no submit {txn_id!r} in this canvas's history", fix="state lists them")

    # -- patch listeners -------------------------------------------------------------------
    def on_patch(self, cb: PatchListener) -> Callable[[], None]:
        self._listeners.append(cb)

        def off() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return off

    def _notify(self, patch: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        meta = dict(meta)
        meta.setdefault("touched", touched_objects(patch))
        if patch or meta.get("kind") == "load":
            self.events.emit(
                "doc.patched",
                {
                    "txn": meta.get("txn"),
                    "patch": patch,
                    "kind": meta.get("kind"),
                    "origin": meta.get("origin"),
                    "touched": meta["touched"],
                },
            )
        for cb in list(self._listeners):
            cb(patch, meta)

    # -- submit --------------------------------------------------------------------------------
    def submit(
        self,
        document: dict[str, Any] | str | None = None,
        *,
        edits: dict[str, Any] | str | None = None,
        origin: str = "user",
        label: str = "",
        since: int | None = None,
    ) -> SubmitResult:
        """Validate a whole canvas document and apply its difference to the running canvas
        as one undo step. Raises ``CanvasInvalid`` (every problem, with fix-its) and leaves
        the canvas untouched when the document is invalid: schema and port-graph problems
        first, then what the locks forbid ``origin`` and the quotas forbid anyone. JSON
        text is accepted so that ids written twice are caught too. A new view under a
        placeholder key is given a name first (ADR 0018); ``renamed`` reports it.

        ``edits`` instead of ``document`` (exactly one): a JSON Merge Patch over the live
        document (``edits.merge_edits``, #98); the merged document then takes the same
        path, so the whole result is validated.

        ``since`` (a ``mark``, the prompt bar's turn start): a non-user submit merges three
        ways with the views and links the user changed after the mark, against their
        turn-start copies (SPEC-18); a field both changed differently is refused with
        ``CanvasConflict``, carrying the live entry (#98). A submit without a ``label``
        gets one from its patch (``diff_text.patch_label``)."""
        self.check_thread()
        if (document is None) == (edits is None):
            raise CanvasInvalid(
                [
                    Problem(
                        path="",
                        message="a submit takes "
                        + ("both a document and edits" if edits is not None else "nothing"),
                        fix="pass exactly one: edits (only what changes) or document (the "
                        "whole canvas document)",
                    )
                ]
            )
        retired = self.retired_ids()
        gone: list[str] = []
        if edits is not None:
            gone = already_removed(self.data, edits, retired)
            document = merge_edits(self.data, edits, retired=retired)
        if isinstance(document, str):
            data, problems = parse_canvas_text(document)
        else:
            data, problems = copy.deepcopy(document), []
        renamed = (
            rename_placeholders(data, self.data["views"], self.data["links"], retired)
            if isinstance(data, dict)
            else {}
        )
        if problems:
            raise CanvasInvalid(problems)
        if isinstance(data, dict):
            watch_the_file_tree(data)  # a tree intaker that does not say watches (#115)
        doc = validate_canvas(data)
        new = doc.to_data()
        if not is_user_origin(origin):
            # the window frame is the app window as the user has it: its origin follows
            # the pan (``navigate``), its size the window; an agent's document, read
            # before the user panned or resized, never drags either back
            new["window_frame"]["rect"] = dict(self.data["window_frame"]["rect"])
            # likewise what the user picked (``remember``): a submitted view that omits
            # params.selected keeps the live value; an explicit value wins
            for vid, view in (new.get("views") or {}).items():
                old_view = self.data["views"].get(vid)
                if old_view is None or not isinstance(view, dict):
                    continue
                kept = (old_view.get("params") or {}).get("selected")
                params = view.get("params")
                if kept is not None and isinstance(params, dict) and "selected" not in params:
                    params["selected"] = kept
                elif kept is not None and params is None:
                    view["params"] = {"selected": kept}
        patch = make_patch(self.data, new)
        if since is not None and not is_user_origin(origin):
            edit = json.loads(edits) if isinstance(edits, str) else edits
            merged, conflicts = self._merge_user_changes(new, patch, since, edit)
            if conflicts:
                raise CanvasConflict(conflicts)
            if merged:
                doc = validate_canvas(new)
                new = doc.to_data()
                patch = make_patch(self.data, new)
        problems = lock_problems(self.data, new, origin)
        problems += reach_problems(self.data, doc, is_user_origin(origin))
        problems += quota_problems(new, self.quotas, self.view_cost)
        problems += file_problems(new, self.project.root if self.project is not None else None)
        if problems:
            raise CanvasInvalid(problems)
        if not patch:
            return SubmitResult(
                empty=True,
                label=label,
                origin=origin,
                renamed=renamed,
                already_removed=gone,
                cursor=self.cursor,
                depth=len(self.history),
            )
        inverse = invert_patch(self.data, patch)
        label = label or patch_label(patch, inverse)
        apply_patch(self.data, patch)
        self._txn_no += 1
        rec = TxnRecord(
            id=f"t{self._txn_no}",
            no=self._txn_no,
            canvas=CANVAS_ID,
            label=label,
            origin=origin,
            patch=patch,
            inverse=inverse,
            touched=touched_objects(patch),
        )
        # a new step drops the redo tail (from the stack and the log)
        dropped = {r.id for r in self.history[self.cursor :]}
        del self.history[self.cursor :]
        self.log = [r for r in self.log if r.id not in dropped]
        self.history.append(rec)
        self.cursor = len(self.history)
        self.log.append(rec)
        self._changes.append((origin, rec.touched))
        entry = self._journal(rec, kind="txn", patch=patch, inverse=inverse)
        rec.ts, rec.seq = entry.ts, entry.seq
        self.dirty = True
        self.events.emit("canvas.submitted", {**rec.summary(), "seq": entry.seq})
        self._notify(patch, {"kind": "submit", "txn": rec.id, "origin": origin, "label": label})
        if self.autosave:
            self.save()
        return SubmitResult(
            txn=rec.id,
            label=label,
            origin=origin,
            patch=patch,
            touched=rec.touched,
            views=_changes(patch, "views"),
            links=_changes(patch, "links"),
            renamed=renamed,
            already_removed=gone,
            cursor=self.cursor,
            depth=len(self.history),
        )

    def check_thread(self) -> None:
        """Catch a change of the canvas off its own thread (SPEC-18). Today every submit runs
        on the GUI thread (the prompt bar's agent tools on the qasync loop, the endpoint's
        ``QLocalServer``, the gestures) or on a thread holding ``serial_lock`` (the SDK's
        test endpoints), which keeps a submit's read, merge and apply whole. Anything else
        raises under tests and warns in the app (``THREAD_CHECK_ENV``)."""
        if threading.get_ident() == self.owner_thread or self.serial_lock.held():
            return
        mode = os.environ.get(THREAD_CHECK_ENV, "warn")
        if mode == "off":
            return
        message = (
            f"the canvas was changed on thread {threading.current_thread().name!r}, neither "
            "the thread that owns it nor one holding its serial_lock"
        )
        if mode == "raise":
            raise RuntimeError(message)
        print(f"[WARN] {message}", file=sys.stderr)

    def retired_ids(self) -> set[str]:
        """View names and link ids the undo history names that the canvas no longer has
        (removed, by anyone, this session or one the journal restored): a placeholder is
        never given one of them, since an undo can bring the old object back and the
        user knows it by that name (#169)."""
        named: set[str] = set()
        for rec in self.log:
            for op in (*rec.patch, *rec.inverse):
                m = OBJECT_PATH.match(op.get("path", ""))
                if m is not None:
                    named.add(m.group(2))
        return named - set(self.data["views"]) - set(self.data["links"])

    def mark(self) -> int:
        """A turn start: ``submit(since=mark)`` merges with what the user changed after it,
        against the views and links as the mark found them (its turn-start copy). Each
        mark has its own id and copy (the prompt bar marks each turn's start once)."""
        self._mark_no += 1
        self._marks[self._mark_no] = (
            len(self._changes),
            copy.deepcopy({"views": self.data["views"], "links": self.data["links"]}),
        )
        for old in sorted(self._marks)[:-MARK_COPIES]:
            del self._marks[old]
        return self._mark_no

    def _merge_user_changes(
        self,
        new: dict[str, Any],
        patch: list[dict[str, Any]],
        since: int,
        edit: dict[str, Any] | None,
    ) -> tuple[bool, list[Problem]]:
        """Merge, in place in ``new``, each view or link ``patch`` changes that a user change
        after the mark ``since`` touched, against its turn-start copy. An edit changes what
        it names (``edits.edit_conflict``); a whole document is merged three ways
        (``edits.three_way``), and a link the user added that it leaves without one of its
        views is a conflict. Returns whether ``new`` changed and the conflicts, each with
        the live entry. ``since`` is a mark id: one no ``mark`` returned (or an evicted
        one) has no copy, so every object the user changed this session is a conflict."""
        start, base = self._marks.get(since, (0, None))
        user = {
            oid
            for origin, touched in self._changes[start:]
            if is_user_origin(origin)
            for oid in touched
        }
        changed = False
        out: list[Problem] = []
        seen: set[str] = set()
        for op in patch:
            m = OBJECT_PATH.match(op["path"])
            if m is None or m.group(2) not in user or m.group(2) in seen:
                continue
            section, oid = m.groups()
            seen.add(oid)
            live_entry = self.data[section].get(oid, MISSING)
            new_entry = new[section].get(oid, MISSING)
            if base is None:
                out.append(_conflict(section, oid, live_entry, [""], edit=edit is not None))
                continue
            base_entry = base[section].get(oid, MISSING)
            if edit is not None:
                clash = edit_conflict(
                    section, oid, edit, base_entry, live_entry, new_entry, new["views"]
                )
                if clash is not None:
                    kind, which = clash
                    out.append(
                        _link_conflict(oid, which)
                        if kind == "link"
                        else _conflict(section, oid, live_entry, which, edit=True)
                    )
                continue
            merged, fields = three_way(base_entry, live_entry, new_entry)
            if fields:
                out.append(_conflict(section, oid, live_entry, fields, edit=False))
                continue
            if merged is MISSING:
                new[section].pop(oid, None)
            else:
                new[section][oid] = merged
            changed = True
        if edit is None and base is not None:
            for lid in sorted(user & set(new["links"])):
                lost = lost_ends(new["links"][lid], new["views"])
                if lost:
                    out.append(_link_conflict(lid, lost))
        return changed, out

    # -- undo / redo ------------------------------------------------------------------------------
    def undo(self, *, origin: str = "user") -> UndoResult:
        if self.cursor == 0:
            raise TxnError("nothing to undo", fix="state shows the history")
        rec = self.history[self.cursor - 1]
        self._apply(rec.inverse, "undo")
        self.cursor -= 1
        return self._moved(rec, "undo", rec.inverse, rec.patch, origin)

    def redo(self, *, origin: str = "user") -> UndoResult:
        if self.cursor >= len(self.history):
            raise TxnError("nothing to redo")
        rec = self.history[self.cursor]
        self._apply(rec.patch, "redo")
        self.cursor += 1
        return self._moved(rec, "redo", rec.patch, rec.inverse, origin)

    def _apply(self, patch: list[dict[str, Any]], action: str) -> None:
        try:
            apply_patch(self.data, patch)
        except PatchError as e:
            raise TxnError(
                f"cannot {action}: the recorded patch no longer applies ({e.message})",
                fix="the document changed outside the submit path; submit it again",
            ) from e

    def _moved(
        self,
        rec: TxnRecord,
        kind: str,
        patch: list[dict[str, Any]],
        inverse: list[dict[str, Any]],
        origin: str,
    ) -> UndoResult:
        self._journal(rec, kind=kind, patch=patch, inverse=inverse, origin=origin)
        self._changes.append((origin, rec.touched))
        self.dirty = True
        self._notify(patch, {"kind": kind, "txn": rec.id, "origin": origin, "label": rec.label})
        if self.autosave:
            self.save()
        return UndoResult(
            txn=rec.id,
            label=rec.label,
            patch=patch,
            touched=rec.touched,
            cursor=self.cursor,
            depth=len(self.history),
        )

    # -- navigation --------------------------------------------------------------------------
    def remember(self, view_id: str, params: dict[str, Any]) -> bool:
        """A view's navigation-class state (what the user picked, ``params.selected``)
        written into its params so a reopened project shows it again (spec #35 story 4).
        Like a pan: journaled as ``navigate``, never an undo step. Returns False when the
        view is gone or nothing changed."""
        view = self.data["views"].get(view_id)
        if view is None:
            return False
        current = view.get("params") or {}
        changed = {k: v for k, v in params.items() if current.get(k) != v}
        if not changed:
            return False
        patch: list[dict[str, Any]] = []
        if "params" not in view:
            patch.append({"op": "add", "path": f"/views/{view_id}/params", "value": {}})
        for k, v in changed.items():
            op = "replace" if k in current else "add"
            patch.append({"op": op, "path": f"/views/{view_id}/params/{k}", "value": v})
        self._navigate(patch, "selection")
        return True

    def navigate(self, origin: tuple[float, float]) -> bool:
        """The user panned: the window frame's origin follows (spec #35 stories 64, 75),
        so the frame guide is where the window is and the quota counts what renders.
        Navigation, not an edit: no undo step, journaled as ``navigate`` so a relaunch
        and a replay see the same document. Returns False when nothing moved."""
        rect = self.data["window_frame"]["rect"]
        x, y = float(origin[0]), float(origin[1])
        if (rect["x"], rect["y"]) == (x, y):
            return False
        patch = [
            {"op": "replace", "path": "/window_frame/rect/x", "value": x},
            {"op": "replace", "path": "/window_frame/rect/y", "value": y},
        ]
        self._navigate(patch, "pan")
        return True

    def _navigate(self, patch: list[dict[str, Any]], label: str) -> None:
        """Apply and journal a navigation patch (kind ``navigate``): no undo step."""
        inverse = invert_patch(self.data, patch)
        apply_patch(self.data, patch)
        entry = JournalEntry(
            seq=self.journal.next_seq(),
            kind="navigate",
            txn="",
            txn_no=self._txn_no,
            origin="user",
            label=label,
            patch=patch,
            inverse=inverse,
        )
        self.journal.append(entry)
        self.dirty = True
        self._notify(patch, {"kind": "navigate", "txn": None, "origin": "user", "label": label})
        if self.autosave:
            self.save()

    # -- turns -----------------------------------------------------------------------------------
    def squash(self, start_cursor: int, *, label: str, origin: str = "agent") -> TxnRecord | None:
        """Fold the records above ``start_cursor`` (the submits of one prompt turn) into
        one undo step with the turn's whole difference, so the reply strip's Undo reverts
        the turn at once (#44). The document does not change, so no patch is emitted.
        Returns the one record for the turn (the existing one when the turn made a
        single submit), ``None`` when it made none. ``TxnError`` when the range is not
        the top of the stack (the agent undid into it, or the cursor is off)."""
        if start_cursor > self.cursor or self.cursor != len(self.history):
            raise TxnError(
                "cannot squash: the turn's submits are not the top of the undo stack",
                fix="undo or redo moved the cursor during the turn; leave the steps as they are",
            )
        recs = self.history[start_cursor:]
        if len(recs) <= 1:
            return recs[0] if recs else None
        before = copy.deepcopy(self.data)
        for r in reversed(recs):
            apply_patch(before, r.inverse)
        patch = make_patch(before, self.data)
        inverse = invert_patch(before, patch)
        self._txn_no += 1
        rec = TxnRecord(
            id=f"t{self._txn_no}",
            no=self._txn_no,
            canvas=CANVAS_ID,
            label=label,
            origin=origin,
            patch=patch,
            inverse=inverse,
            touched=touched_objects(patch),
        )
        gone = {r.id for r in recs}
        del self.history[start_cursor:]
        self.history.append(rec)
        self.cursor = len(self.history)
        self.log = [r for r in self.log if r.id not in gone] + [rec]
        entry = JournalEntry(
            seq=self.journal.next_seq(),
            kind="squash",
            txn=rec.id,
            txn_no=rec.no,
            origin=origin,
            label=label,
            patch=patch,
            inverse=inverse,
            touched=rec.touched,
            squashed=[r.id for r in recs],
        )
        self.journal.append(entry)
        rec.ts, rec.seq = entry.ts, entry.seq
        self.events.emit("canvas.squashed", {**rec.summary(), "squashed": entry.squashed})
        return rec

    # -- journal / disk ------------------------------------------------------------------
    def _journal(
        self,
        rec: TxnRecord,
        *,
        kind: str,
        patch: list[dict[str, Any]],
        inverse: list[dict[str, Any]],
        origin: str | None = None,
    ) -> JournalEntry:
        entry = JournalEntry(
            seq=self.journal.next_seq(),
            kind=kind,  # type: ignore[arg-type]
            txn=rec.id,
            txn_no=rec.no,
            origin=origin or rec.origin,
            label=rec.label,
            patch=patch,
            inverse=inverse,
            touched=touched_objects(patch),
        )
        self.journal.append(entry)
        return entry

    def save(self, *, force: bool = False) -> None:
        """Write the canvas when it changed. A disk error (a sync client holding
        ``canvas.json``, WinError 5) never reaches the caller: the canvas stays dirty,
        warns once (``[WARN]`` and a ``canvas.save_failed`` event) and the next save
        tries again."""
        if self.project is None or not (self.dirty or force):
            return
        try:
            self.project.save_canvas_document(self.data, quick=self.save_error is not None)
        except OSError as e:
            first = self.save_error is None
            self.save_error = e
            if first:
                self._disk_denied(e, self.project.canvas_file, "the canvas")
            return
        if self.save_error is not None:
            print(f"[OK] {self.project.canvas_file} saved again")
            self.save_error = None
        self.dirty = False
        self.events.emit("canvas.saved", {})

    def _disk_denied(self, e: OSError, path: Any, what: str) -> None:
        """One warning per streak of denied writes; the state stays in memory."""
        fix = "close whatever holds the file (a sync client, a scanner); the next change saves it"
        print(f"[WARN] {what} was not written to {path}: {e.strerror or e}; changes stay in memory")
        self.events.emit(
            "canvas.save_failed", {"path": str(path), "error": str(e), "what": what, "fix": fix}
        )
