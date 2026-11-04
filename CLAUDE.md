# Specto

Personal desktop viewer for agent-produced data, agent-first with a minimal canvas: the agent composes views and links, the user looks, interacts and arranges. Vocabulary: `GLOSSARY.md`. Decisions: `docs/adr/` (0011-0017 are the v2 set and supersede `docs/review/hld.md` where they conflict). Background: `DESIGN.md`. Package contracts: `docs/contracts.md`.

## How work is done

Read `docs/agents/workflow.md` before doing anything. In short: every request becomes a board task first. A builder takes one task in its own worktree off `main`, works test-first and opens a pull request. The lead reviews it on two axes, then fast-forwards `main` and closes the task.

## Agent skills

### Issue tracker

Issues and specs live as tasks on the maintainer's private Backlog.md board (the `backlog` CLI, found through `BACKLOG_CWD`), not in this repo. GitHub Issues are only a public inbox that gets copied onto the board; never work from one directly. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default triage roles, label string equal to role name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
