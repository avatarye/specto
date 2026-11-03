# Issue tracker: Backlog.md (private)

Issues and specs live as tasks on the maintainer's private [Backlog.md](https://github.com/MrLesk/Backlog.md) board, kept in a separate private repository. They aren't in this repo. GitHub Issues are turned off, so text from strangers never reaches an agent as a task.

The `backlog` CLI finds the board through the `BACKLOG_CWD` environment variable, so commands work from any checkout or worktree. Task ids look like `SPEC-12`.

## Conventions

- **Create a task**: `backlog task create "<title>" -d "<description>" -l <labels> --ac "<criterion>"` (repeat `--ac` once per criterion). On Windows, call the CLI through `node <npm root -g>/backlog.md/cli.js`: the npm shim cuts multi-line arguments at the first newline.
- **Read a task**: `backlog task view SPEC-12 --plain`, or read its file in the board's `backlog/tasks/`.
- **List tasks**: `backlog task list --plain` (filter with `-s "<status>"` or `-l <label>`).
- **Edit a task**: `backlog task edit SPEC-12` with `-s "In Progress"`, `-l`, `--ac`, `--notes`, `--plan`.
- **Sub-tasks**: `backlog task create ... -p SPEC-12` makes a child of SPEC-12.
- **Dependencies**: `--dep SPEC-7`.
- **Close**: `backlog task edit SPEC-12 -s Done --final-summary "<cause, fix, merge commit>"`.
- **Statuses**: `To Do`, `In Progress`, `Done`.

The board's own repository commits its changes. The board is the only place task text lives.

## Linking to this repo

Commits and pull requests in this public repo name the task id only (`Refs SPEC-12`). They never copy task text beyond what the change itself needs. Plain `#NNN` numbers in code and docs refer to the prior GitHub tracker; the board's `backlog/docs/github-issue-map.md` maps them to task ids.

## When a skill says "publish to the issue tracker"

Create a Backlog.md task.

## When a skill says "fetch the relevant ticket"

Read it as in **Read a task** above.

## Specs and maps

- **Spec**: a task labelled `spec`, with its tickets as sub-tasks (`-p`).
- **Map** (wayfinder): a task labelled `wayfinder:map`, with child tasks as tickets and blocking edges as `--dep`.
- **Claim**: set the task `In Progress` and an assignee (`-a @me`).
- **Resolve**: add notes, then set it `Done` with a final summary.
