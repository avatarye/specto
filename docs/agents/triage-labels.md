# Labels

Tasks on the board carry one type label and one triage label. The board's `backlog/config.yml` lists them all.

## Type

| Label           | Use for                                         |
| --------------- | ----------------------------------------------- |
| `bug`           | Something that worked, or should, and doesn't   |
| `enhancement`   | New behaviour                                   |
| `documentation` | Docs only                                       |
| `chore`         | Tooling, cleanup, no behaviour change           |
| `spec`          | A spec-sized request, split into sub-tasks      |

## Triage

The skills speak of five triage roles; the label string equals the role name.

| Role / label      | Meaning                                                              |
| ----------------- | -------------------------------------------------------------------- |
| `needs-triage`    | The maintainer has to evaluate it (every public-inbox report starts here) |
| `needs-info`      | Waiting on an answer from the maintainer or the reporter             |
| `ready-for-agent` | Fully specified; a builder may take it                               |
| `ready-for-human` | Needs the maintainer's hands (hardware, accounts, judgement)         |
| `wontfix`         | Will not be done; the final summary says why                         |

Only the maintainer moves a task from `needs-triage` to `ready-for-agent`. When a skill mentions a role, for example "apply the AFK-ready triage label", use the label in this table.
