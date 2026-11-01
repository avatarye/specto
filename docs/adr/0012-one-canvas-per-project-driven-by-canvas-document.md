---
status: accepted
date: 2026-10-17
---

# One canvas per project, driven entirely by its canvas document

## Context

The HLD allowed many canvases per project and treated the canvas document as the record that commands patch. In the agent-first UX (ADR 0011) the agent composes the surface, and the user wants that composition to be one artefact the agent manipulates.

## Decision

A project is a data folder with exactly one canvas. The canvas is described completely by one JSON canvas document: intakers, views, links and layout. The running canvas is driven entirely by that document. Intaker and link logic uses Specto's fixed API vocabulary where it covers the need; Python scripts embedded in or referenced from the document add flexibility where it does not.

## Consequences

- The canvas document becomes the agent's main target; its schema is a public contract and must be versioned (schema_version, migrations).
- Multi-canvas features (canvas.ls, canvas switching) leave scope; addresses keep the canvas component (ADR 0010) but it is always the project's one canvas.
- How edits reach the running app (agent writes the file and the app reconciles, versus commands that patch it) is an open decision on the UX realignment map.
- Embedded Python falls under the trust rules (ADR 0006).

## Considered options

- Many canvases per project (HLD 3.1/5.6): rejected for simplicity.
- Python-only compositions: rejected; JSON stays the reviewable, diffable default.
