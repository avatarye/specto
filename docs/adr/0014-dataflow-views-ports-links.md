---
status: accepted
date: 2026-10-17
---

# Views with typed ports, connected by links; intakers are links

## Context

The HLD had separate concepts: intake specs producing collections, and links sharing channels between views. The user's examples (a gallery selection choosing the matching video for a player; GeoJSON file contents feeding a map) are one pattern: a value from one view, transformed, drives another.

## Decision

The canvas is a directed, acyclic dataflow graph. Views expose typed input and output ports. A link maps an output port's value, through a built-in mapping or Python code, to another view's input port. An intaker is a built-in link kind whose output is a collection. The file tree is a view whose intaker is the project folder and whose outputs (all files, selected files) feed other views' intakers. The starter port types are all files, selected files, file contents and time; the set grows with use. Only some port types allow two-way links (time, file selection). Cycles are forbidden on the port graph (each view type declares which inputs change which outputs); two-way links are the only sanctioned loops. An input port takes at most one link. Follow links match by key, optionally on one key field (for example frame). Each link kind has its own model, so the JSON Schema shows its rules.

Links never run per rendered frame: they resolve when their logic or inputs change (ADR 0016), and at runtime only look up resolved results. Python link code runs out of process (ADR 0006).

## Consequences

- One wiring concept for agents and users; the canvas document holds views and links only.
- The M1/M2 channel model (views sharing a channel) is replaced by directed links; existing channel code is migrated or wrapped.
- Python link latency needs measuring (a prototype ticket on the UX realignment map).

## Amendment 2026-10-18: intaker scope (selection or pinned)

In the first user test the agent composed every view from the file tree's `all_files` port with `sources` pinned to one run folder, so changing the file selection changed nothing downstream, although the runtime followed `selected_files` correctly. The decision was left to convention (which port to wire), and the agent picked the one that made its summaries true. Decision: views fed from the file tree **follow the selection by default**, and the agent must **distinguish the two cases explicitly**. An intaker whose source is a file tree carries `scope`:

- `selection`: fed from `selected_files`; screener, keys, grouping and sort apply to whatever the user picks (a folder pick expands to its files); `sources` default to `.` and only narrow the picks. The default for a view placed next to the file tree. While nothing is picked the view shows the empty-state line "Pick files in the file tree" (the host registry sets it for any empty value delivered off `selected_files`, so a `read_contents` of the picks says it too).
- `pinned`: fed from `all_files` (or the project folder); the view always shows `sources`, whatever is picked. Only when the user names specific data ("always show the 02 run").

Validation requires the field when the source is a file tree and pairs it with the source port (`selection` <-> `selected_files`, `pinned` <-> `all_files`), with fix-its naming the other choice; the project folder's intaker has no scope. `describe link:intaker` lists both scopes with one example each, the six tools' descriptions and the prompt bar's system prompt state the default and the one reason to pin, and the agent says which it chose in the link summary. Alternatives rejected: inferring the scope from the port alone (the user test showed the agent then never asks itself the question) and a per-view "follows selection" flag (the intaker is where the files are chosen, so the choice lives on the link).

## Considered options

- Keep intakers and links separate: rejected; two concepts for one pattern.
- Allow general cycles with echo suppression: rejected; only the two-way port types above.
