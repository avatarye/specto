---
status: accepted
date: 2026-10-12
---

# Trust is the user's approval of a content hash; build.py and SDK scripts run out of process

## Context

Recipes and canvases can carry code (`build.py`, `transforms.py`, Python views, web-view JS). The threat model for one user is (1) a canvas or recipe pulled from someone else's repository and (2) a prompt-injected agent writing recipe or panel code locally (HLD 3.11, review finding R9). `DESIGN.md` had adopted Jupyter's HMAC-signed trust database with locally created recipes trusted automatically, and an in-process SDK mode for `build.py`.

## Decision

Trust is the user's decision on a content hash, regardless of who wrote the content. The store is a plain hash list in the user data dir, with no HMAC (nothing can write there without already running as the user). Code-running paths are enumerated and there are no expression fields; untrusted content opens in Restricted Mode (layout and data load, code does not). `build.py` and every SDK script run out of process through the remote SDK, even when the app launches them. Web views run under Chromium's sandbox plus a per-view capability list enforced on the Python side. There is no "execute arbitrary code" tool (section 10 `trust`).

## Consequences

- An agent writing a recipe locally does not make it trusted; the user still clicks Run on that exact content.
- No command approves (#203): the endpoint's `link.approve`, which the CLI and any local process can call, only opens an approval card in the window naming the requester; the user's click on it, or on the view's Approve, is the approval. Headless tests stand in for that click with the repo conftest's `approve_link` fixture, never a flag.
- The SDK has one mode, remote; "headless" means core plus intake plus readers without a GUI, and `snap` always needs a running app (HLD 2, R11).
- A bad script cannot crash the app.
- The code-running paths are listed in contracts section 10: Python links and xacro expansion (SPEC-43.3, whose `${...}` is a Python eval). Each runs approved content only, in its own `python -I` process whose whole tree a timeout kills. xacro's approval covers the root's bytes together with its args and, per root, each package folder `$(find)` resolves (an agent can write both); the environment is refused and the child starts without it; every file it includes or loads as YAML is checked by its path text before it opens, confined to the project and the approved packages, and hashed.
- Capabilities are built by the app from a view's manifest and bindings, never from the page or its state (contracts section 17).
- Not yet implemented in code as of M2 (no trust store or Restricted Mode found under `packages/`); recipes with code are M4.

## Considered options

- **HMAC-signed trust database, locally created content trusted (Jupyter).** Rejected: the HMAC adds nothing against an attacker already running as the user, and "locally created" includes a prompt-injected agent.
- **In-process SDK for `build.py`.** Rejected: a crashing or hostile script would run inside the app.
- **Expression fields in layouts.** Rejected: another unenumerated code path.
