---
status: superseded by 0011
date: 2026-10-13
---

# A complete human GUI underneath, the agent layer on top

## Context

M1 proved the agent loop end to end (an MCP client composing linked galleries and verifying with `state` and `snap`), but the user's review of M1 was "there is no user flow, and the GUI is buggy" (HLD section 10). An agentic GUI whose only real author is the agent leaves the user unable to fix, extend or even reproduce what the agent built, and leaves the shared surface untested by a human.

## Decision

Specto is a complete human-usable desktop app on its own: open a project, browse files, intake with live previews, create views, dock, link, undo and diff, with no CLI and no agent. The agent layer sits on top. Every human action goes through the same commands an agent calls, so both share one surface, one canvas document and one undo history (HLD principle 5, user decision 2026-10-13). M1.5 was inserted before M2 to build this, with a scripted real-mouse-and-keyboard test as its exit.

## Consequences

- Every GUI gesture is one user command or one labelled transaction, journaled with origin `user` and visible to the agent through `state` and provenance.
- New features need a human path as well as a command; a command-only feature is incomplete (inferred from principle 5).
- Gesture traffic forced undo-stack hygiene: selection, navigation and window resizes stay off the undo stack (`undoable=false`, contracts section 3).
- Shell tool areas (file browser, intaker) exist as side panels outside the canvas.

## Considered options

- **Agent-first, minimal GUI (the original M1 scope).** Rejected after the M1 review.
- **A separate human GUI that writes the document directly.** Rejected: two write paths, two undo histories (inferred; principle 1 already forbids private paths).
