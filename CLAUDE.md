# Specto

Personal desktop viewer for agent-produced data, agent-first with a minimal canvas: the agent composes views and links, the user looks, interacts and arranges. Vocabulary: `GLOSSARY.md`. Decisions: `docs/adr/` (0011-0017 are the v2 set and supersede `docs/review/hld.md` where they conflict). Background: `DESIGN.md`. Package contracts: `docs/contracts.md`.

Active work: open tasks labelled `spec`; each is built with `implement-spec` on an integration branch named after it.

## Agent skills

### Issue tracker

Issues and specs live as tasks on the maintainer's private Backlog.md board (the `backlog` CLI, found through `BACKLOG_CWD`), not in this repo. GitHub Issues are off. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default triage roles, label string equal to role name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
