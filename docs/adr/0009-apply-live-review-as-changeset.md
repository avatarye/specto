---
status: accepted
date: 2026-10-12
---

# Agent changes apply live and are reviewed as changesets; no proposal mode

## Context

The chosen visual direction (ADR 0008) shows agent turns as proposals, ghosted until accepted. A proposal adds a second state (applied vs proposed) to every command, every `state` read and every `snap`, and the agent can no longer verify its own work against what is really on the canvas (HLD 6, `gui-styles.md` risks).

## Decision

Commands apply to the live document immediately. Each committed transaction is a changeset: highlighted on view headers and overworld cards, revertable per change or per transaction, and diffable in the Monaco diff view. Proposal mode is not in the core model; if wanted later it is an uncommitted dry run shown on the canvas (HLD section 10, `review`: apply live plus changeset review in M1).

## Consequences

- The agent's verification (`snap`, `state`, `events.poll`) sees exactly what the user sees.
- Protection of user edits comes from locks and provenance, not from proposals.
- Dry run is the one place a "not yet landed" state exists, and it never touches the document.

## Considered options

- **Proposal mode in M1.** Rejected: a proposed state in every command and snap.
- **Proposal mode as an M2 experiment on dry run.** Left open as the way back in.

This ADR is an addition beyond the originally requested list: it records the deviation from B Calm Canvas that a future reader would otherwise "fix".
