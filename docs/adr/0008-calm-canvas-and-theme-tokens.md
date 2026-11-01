---
status: accepted
date: 2026-10-12
---

# B Calm Canvas visual direction; theme tokens are the only colour source

## Context

`docs/review/gui-styles.md` offered four directions: A Studio (dense dark pro tool), B Calm Canvas (light whiteboard overworld, agent changes as proposals), C Patch/Perform (wires first) and D Desk Strip. The author recommended A. Agents also choose styling when they compose views, and generative-UI systems that let models invent colours produce inconsistent results (the A2UI catalogue lesson).

## Decision

The user rated B 5 and the others 1, so B Calm Canvas is the visual language (HLD section 10). Theme tokens (colours, glyphs, metrics, fonts) are owned by the app and are the only source of colour: QML reads them from a `theme` context object, web views and the CLI read them from `api-info.json`, and agents pick from them. No raw colours in QML or views.

## Consequences

- B's proposal mode was not adopted (see ADR 0009); live agent changes use B-style highlights instead, glyph plus colour (never colour alone), with ghosted before-states for removals.
- Light styling must coexist with dark 3D and video content; the risk is named in `gui-styles.md` and not yet resolved (inferred to be open).
- Tokens are part of the vocabulary agents read, so renaming a token is an API change for web views and agents.
- Cross-cutting items carried with B: one-key overworld toggle with hold-to-peek, change review, locks, command palette, keyboard model, provenance on cards, a non-blank empty canvas.

## Considered options

- **A Studio**, the author's recommendation: denser and suited to dark content, rejected by the user's rating.
- **C Patch/Perform** and **D Desk Strip**: rated 1.
- **Free styling by agents.** Rejected: tokens only.
