---
status: accepted
date: 2026-10-17
---

# The agent edits the canvas document directly; a pydantic model validates it

## Context

ADR 0012 makes the canvas document the single description of a project's canvas and the agent's main target. Routing every agent change through fine-grained commands is slow for an agent composing many views and links at once.

## Decision

The canvas document's format is a pydantic model (its JSON Schema is exported for agents). The agent may write the JSON directly, as often as it likes during a manipulation; when it emits a submit signal (detected by a hook on the agent session), the app validates the submitted document against the model, rejects invalid documents with errors while keeping the last good state, and applies the difference to the running canvas. One submit is one undo step, and Specto's undo/redo covers unwanted manipulations. Commands remain as helpers that patch the same document. Human gestures (move, resize, snap) also write the document.

## Consequences

- Validation must be strict and its errors agent-readable (fix-it messages).
- Undo history is the document's history, regardless of who changed it.
- The reconcile step (diff old vs new document, apply to live views) becomes a core module with its own tests.

## Amendment 2026-10-18: one prompt turn is one undo step

The spec (#35, Implementation Decisions) says "one prompt turn = one undo step", and the prompt bar's agent commonly submits several times in one turn (a rejected document costs nothing, but a corrected second submit and a follow-up adjustment are both applied). The rule above stands per submit: each submit is validated and applied on its own and is one step while the turn runs. When the turn ends, the steps it added on top of the stack are folded into one (`LiveCanvas.squash`, journal kind `squash`), so the reply strip's Undo and Ctrl+Z revert the whole turn at once. External agents over MCP have no turn boundary: their submits stay one step each. Human gestures are one step each. Navigation (the window frame following a pan, `LiveCanvas.navigate`) changes the document without a step at all.

## Amendment 2026-10-23: a submit is a whole document or an edit of the live one (#98)

Measured on the bench after #73: a one-field move cost about 660 output tokens and 5.2 s on Sonnet, because `submit_canvas` took the whole document and the model retyped every view and link to change one position; the cost grew with the canvas. Decided with the user on spec #98 (all eleven recommendations, option A):

1. **Edits.** `submit_canvas` takes `edits` or `document`, exactly one (both or neither is refused with a fix-it). `edits` is a JSON Merge Patch (RFC 7396) in the document's own shape: objects merge key by key, any other value (a list too) replaces, `null` removes, and whatever the edit leaves out stays as it is live. The agent is told to prefer it (tool doc, system prompt, standing reference); `document` remains for rewrites and for clients that already send whole documents.
2. **One submit path.** The edit is merged into a copy of the live document (`specto_core.edits.merge_edits`) and the result takes the unchanged submit path: placeholder naming, whole-document validation (every problem at once), locks, reachability, quotas, unknown files, `make_patch` against the live document, one undo step, one journal entry, squash per prompt turn. The stored patch is always `make_patch(live, merged)`, never the agent's text, so undo, the journal, the outline and the strip's diff keep their form.
3. **What an edit may name.** `views`, `links`, `root_view` and `project.label`. `schema_version` and `window_frame` are refused, as are `views: null` and `links: null` (they would drop everything), any other top-level key, and an id the canvas does not have unless the entry is a whole new object (`type` for a view, `kind` for a link); a misspelt name is therefore never a silent new view.
4. **Removal cascades.** `views: {x: null}` also removes every link into or out of `x` and clears `root_view` if it was `x` (as the close gesture does); the result's `links.removed` lists them.
5. **The turn-start conflict rule (both forms).** The prompt bar marks each turn's start (`LiveCanvas.mark`) and submits with `since`. A non-user submit whose patch touches a view or link that a user change after the mark touched (a gesture's submit, an undo or redo; navigation does not count) is refused with `CanvasConflict` (code `conflict`), one problem per object carrying its live entry. Objects the user did not touch merge as usual. The refusal does not count toward the three rejected submits of a turn. This also ends the latent revert of whole-document submits, which used to diff a view the user dragged mid-turn back to the agent's turn-start copy: such a submit is now refused instead. External MCP clients have no turn and get no check (a `base` hash is deferred until one edits concurrently with the user).
6. **A mid-turn user step stays the user's.** The squash at the turn's end folds only the turn's submits above the last user step, so the strip's Undo reverts the agent's change and leaves the user's drag in place.
7. **No closing reply after a change** (user decision). A submit that changed the canvas ends the prompt turn: a PostToolUse hook on `submit_canvas` stops the model (`continue: false`), so the second model call that wrote a one-line reply is gone; the strip shows the submit's label as the reply. A rejected or empty submit lets the model go on.

Measured on Sonnet (bench, `tools/bench/prompt_bench.py`): the move from 705-719 output tokens and 5.2-5.6 s to 115 tokens and 2.1 s; the new gallery from 1098-1146 tokens and 7.2-8.1 s to 392 tokens and 3.8 s (numbers on #98).

Considered and not chosen: a JSON Patch (RFC 6902) argument (the same cut, but the model writes op lists and paths instead of the shape it reads); a seventh tool `edit_canvas` (the same cut, at the cost of ADR 0017's six-tool contract and a drift toward per-gesture tools).

## Considered options

- Commands only (HLD 3.4): rejected as too slow for agent composition.
- Unvalidated direct edits: rejected; a broken document must never reach the live canvas.
