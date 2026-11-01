---
status: accepted
date: 2026-10-12
---

# Undo is the inverse of a document patch; transactions belong to the connection that began them

## Context

Agents compose a viewer through many commands, and the user must be able to undo, review and diff that work as one unit while also editing by hand (review finding R3). `DESIGN.md` had adopted the Kit/Qt pattern: commands that implement `undo()` go on a `QUndoStack`, others stay off it. That puts Qt in core, makes every command author write an undo, and leaves an agent's turn only partly undoable when any command lacks one.

## Decision

Handlers change the canvas document only through a recorder that writes each change as an RFC 6902 JSON Patch operation plus its inverse. Undo applies a transaction's inverse patch, redo its forward patch; no command writes `undo()` and `QUndoStack` is not used (HLD 3.4).

Every command runs in a transaction; a call outside `txn.begin` is its own transaction. The connection that began a transaction owns it; disconnect or `ttl_s` idleness aborts it. The undo stack is linear over transactions, which is the whole conflict rule between user and agent. Commands with effects outside the document are refused unless the transaction is begun with `allow_irreversible`, and are then marked irreversible.

HLD section 10 records `txn` as "not answered; default stands, revisit if it hurts".

## Consequences

- Core has no Qt dependency; the GUI reacts to patches, never to commands, so undo is not a special case for views.
- The canvas document must be JSON (patches are the undo and diff unit), which is why it is not TOML.
- The same patch feeds the journal, the changeset highlight and the Monaco diff view.
- MCP is stateless, so the MCP server keeps an agent-session to open-transaction map; the CLI uses named connections (`cli:<user>@<host>`) so a transaction spans several CLI processes and survives socket close, relying on `ttl_s` (contracts section 2).
- M1.5 added `undoable=false` commands (selection and channel traffic, navigation, window resizes) that apply and journal but stay off the undo stack; undoing an older transaction may then hit stale inverse ops, which are skipped and listed (contracts section 3).
- A `deferred` transaction mode exists for all-or-nothing composition (M1 exit criterion 4), alongside the HLD's `live` default.

## Considered options

- **Per-command `undo()` on `QUndoStack` (Kit style).** Rejected: Qt in core, an undo per command, partial undo of an agent turn.
- **Proposal mode instead of live application.** See ADR 0009.
