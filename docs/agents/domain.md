# Domain docs

How agents use this repo's domain documentation. Specto has a single context.

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root: the project's vocabulary.
- **`docs/adr/`**: the decisions. Read the ADRs that touch the area you are about to work in. 0011-0017 are the v2 set and supersede `docs/review/hld.md` where they conflict.
- **`docs/contracts.md`**: the package contracts (ports, commands, the host context, the snapshot fields).

## Use the glossary's vocabulary

When your output names a domain concept (a task title, a test name, a docstring, a hypothesis), use the term as `GLOSSARY.md` defines it. Don't drift to the synonyms an entry lists under _Avoid_.

A concept with no entry is a signal: either the project doesn't use that language (reconsider), or there is a real gap. Add the entry with the `domain-modeling` skill in the same change.

## Record decisions

A decision that later work must respect gets an ADR: the next free number in `docs/adr/`, checked against `main` just before you commit, since parallel branches can pick the same one. A change that alters an earlier ADR adds an "Amended" note to it.

## Flag ADR conflicts

If your output contradicts an existing ADR, say so explicitly rather than silently overriding it:

> _Contradicts ADR 0013 (the canvas document, reconciled), but worth reopening because..._
