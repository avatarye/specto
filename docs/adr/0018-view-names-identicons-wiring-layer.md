---
status: accepted
date: 2026-10-18
---

# Views are named, ports show identicons, and curves move to a wiring layer

Amends ADR 0014 on how links are presented; the model (views with typed ports, directed links, one link per input port) is unchanged. Agreed with the user in the v2 user test (#51), spec #78.

## Context

Views were hard to tell apart and hard to name to the agent: a header carried an id like `view:v3`, the type and the in/out counts; link curves cluttered the normal view; hovering a port showed a link object the user had to map back to views. The user talks to the agent about views ("make this one bigger", "link the rear gallery to the video") and needs one handle both sides understand without a lookup table.

## Decision

1. **View name.** Every view has a short pronounceable name (`kobi`, `tamu`) and the name IS the view id: one handle for the user, the agent and the document. A name is two consonant-vowel syllables from a restricted alphabet (`names.py`); the app generates names, they are unique within the canvas and never change. The file tree keeps the fixed id `files` (like `project` for the project source). The free-text label stays as a very brief summary of a few words written by the agent; the header reads `name: label` (#80).
2. **Identicon.** Each view has an identicon: a GitHub-style 5x5 horizontally mirrored grid generated from a hash of the name, coloured by the view's link chain so related views still read as a group.
3. **Ports show identicons; no curves in the normal view.** An input port shows the identicon of the upstream view that feeds it; the output port shows the view's own. A view with several linked inputs shows one input circle per linked input, stacked on the left edge (#81).
4. **A link is the filter on its input port.** Already true in the model (one source, one target, one link per input port). Hovering an input port shows the link's summary and the port's data type; hovering an output port shows its data type. A two-way link is a filter on both ends; each end's hover says "two-way with <name>" (#81).
5. **Wiring layer.** The backtick/tilde key toggles the wiring layer over the normal view: views dimmed, ports, links and curves drawn, link nodes open link summaries. The overworld keeps its wires (#82).

### How a new view gets its name

The agent submits a new view under **any placeholder key** (`new1`, `v9`, `rear_gallery`): anything that is neither a view of the running canvas nor in name form. `submit` names it before validation, rewrites the links and `root_view` that reference the placeholder, and reports the mapping as `renamed` in the submit result; the next canvas context carries the real names. A key already in name form that the canvas does not have is kept (the agent is re-adding a view it saw, for example after an undo).

Chosen over the alternative, `state` handing out fresh names for the agent to use, because it keeps the agent's submit a single call with no failure mode: an agent that forgets to ask, or invents its own id, still lands its document in one submit, and the placeholder it chose is meaningful to it until the result arrives. The cost is one indirection (the agent reads the mapping once) and a validation rule that plain `validate_canvas` keeps strict: outside `submit` a placeholder key is a rejected document with a fix-it saying so.

### Legacy ids

A canvas saved before this decision has `v<n>` ids. On open it is renamed once: the file tree becomes `files`, every other view gets a generated name, links and `root_view` are rewritten, and one journal entry of kind `rename` records the whole difference. It is not an undo step, and the undo history recorded under the old ids is dropped (`history_note` says why). The project and the file tree keep their fixed ids.

### Colour rule

An identicon's cells take the view's link chain token (`chain_<n mod 6>`) and sit on that token's soft tone (`chain_<n>_soft`); a view in no chain takes the neutral `chain_none` on `surface_alt`. The grid comes from the name alone (the first 15 bits of the name's SHA-256 fill the left three columns, the right two mirror them; a blank grid lights its centre), so a view's shape is stable across canvases and themes while its colour says which chain it is in on this canvas. QML asks the provider for `image://identicon/<name>?chain=<n>`.

### Name generation

`fresh_name(taken, salt)` hashes the set of taken names and a salt (the placeholder key, a gesture's file path) into the sorted pool and probes forward past taken names: deterministic for equal inputs, so a renamed submit lands the same way twice and tests can predict it, with no generator state to keep. The pool (about 5500 names) excludes repeated syllables (`baba`) and a short list of words that read badly or are document vocabulary (`data`, `file`, `node`, `time`).

## Consequences

- The schema's view-key pattern is the name grammar plus `files`; a document written by hand with other keys is rejected outside `submit`.
- Every surface that showed `v<n>` (tools' descriptions, fix-its, the prompt bar's system prompt, `describe canvas`) talks in names; the agent is told to call views by name and keep labels to a few words.
- Test fixtures and GUI flows name views (`files`, `kobi`, `tamu`, ...); gesture tests read the generated name off the result rather than pinning it.
- The link chain's colour lives in identicons (and, in the wiring layer and overworld, in wires); view frames in the normal view no longer need the chain stroke (#80).

## Considered options

- `state` returns fresh names, the agent uses them: rejected (see above) for the extra call and the reject-and-resubmit path when the agent invents an id.
- Agent-chosen names: rejected; names would drift into labels ("front_gallery_2"), collide, and change.
- Keep `v1` for the file tree: rejected; "every view has a name that is its id" would have one exception, and `v1: Files` in a header reads as the legacy id it is. `files` is fixed, readable and not in the grammar, so it is never generated.
- A random generator: rejected in favour of the hash-and-probe rule for reproducibility; the names are as arbitrary either way.

## Amendment 2026-10-19: the project source is a drawn node and the one source of all files (#83)

The user asked for "a special root node that has all files under the project and has only one output: all files; all other views must be children of it; it cannot be moved". Decided with the user on spec #78:

1. **Term.** It stays the *project source* (glossary); "root view" keeps meaning the player other views follow for time.
2. **Drawn, fixed.** The project source is drawn as a node with the Specto mark and no card body, fixed at the canvas origin left of the file tree (a fresh canvas puts the file tree at x = 80). It pans with the canvas, Home brings it into view, and the overworld shows it the same way. It is not a view: `project` is never a key of `views`, so no submit can move, resize, delete or lock it, and the UI gives it no header, handles or menu.
3. **One output.** Its one output port is `all_files` (the project intaker, watched). The file tree takes it as input and keeps one output, `selected_files`; pinned intakers hang off `project.all_files`. Hovering the mark or its port says "all files" and the count. The node itself shows the Specto mark; its output port circle, and every input circle it feeds (the file tree's, every pinned intaker's), shows a generated identicon seeded with the name `root` (user decision). The id stays `project`; `root` is only the identicon seed. Hubs are neutral (user decision): the project source and every file tree feed many chains, so their identicons take `chain_none` wherever they show, on their own port circles and on every input circle they feed. Ordinary views keep the colour rule above (#92).
4. **Reachability binds the agent, like a lock** (user decision). An agent submit that leaves a view unreachable from the project source by links (both ways along a two-way link), or adds one, is refused, naming the view and suggesting the link (an intaker off the file tree's picks or the project source, a `read_contents` for a map, or "link its upstream view" when another unreachable view feeds it). The user's gestures are not bound: deleting a view's only link, or closing a view another follows, goes through, and the orphaned view shows the health `disconnected` with the status-bar line "<name>: not connected · link it from the file tree or ask the agent" until a link reaches it again. A view the user left unconnected does not block an unrelated agent submit. Plain validation (`validate_canvas`, loading) does not judge reachability.
5. **Existing canvases.** On load, links from a file tree's `all_files` (and from the old `project.folder`) move to `project.all_files`, and a layout whose file tree sits on the node's spot moves right as a whole (the leftmost view lands at x = 80); one journal entry of kind `rewire` records the difference. It is not an undo step, and the undo history before it is dropped, as with the rename above.

`project.all_files` is the whole folder as a value (every link off it scans its own sources and follows arrivals with its own `watch`); the list of files the project intaker found is kept beside it for the node's count and the prompt bar's folder summary.

## Amendment 2026-10-19: the file tree has a generated name (#87)

The user: "all views including the file browser get a unique name". This replaces the fixed id `files` of decision 1 and the legacy rule above.

- The file tree's id is a generated name like every other view, with its own identicon. No view id is reserved; the view-key pattern is the name grammar alone. Only the project source keeps a fixed id, `project`, and it is not a view.
- A fresh canvas names its file tree with `fresh_name` over an empty canvas and the salt `file_tree`, so every new canvas starts with the same tree name (`fege`). Nothing depends on that: code finds the file tree by its type (`file_tree`; `canvas_document.file_tree_of`), never by id.
- On open, a canvas whose file tree is `files` (saved between #79 and #87), or a legacy canvas with `v<n>` ids, gets every non-name view id renamed once: the file tree like any other view, with links and `root_view` rewritten. This happens in the same journalled load rewrite as #83's: one entry, of kind `rewire` when links or the layout moved too (`rename` when only names changed). It is not an undo step, and the history before it is dropped.
- `files` is no longer valid in a document. Plain validation refuses it with the name fix-it; a submit treats it as a placeholder and names it.

## Amendment 2026-10-23: new links under placeholder keys too (#98)

With edits (ADR 0013 amendment), a submit merges into the live document, so a new link written under an id the agent picked (`l2` meant as new) would merge silently into the live `l2`. New links therefore go in under placeholder keys like new views: at submit, a link key that is neither a live link nor in the form `l<n>` gets the next free `l<n>` (counting the live links and the submitted ones), and the result's `renamed` maps it (`new2 -> l3`) beside the view names. Nothing refers to a link by id, so only the key changes. Outside `submit`, plain validation still refuses a non-`l<n>` key, with a fix-it saying to submit it under a placeholder.

## Amendment 2026-10-23: the file tree auto-updates by default, one load rewrite per project (#115)

User decision: the file tree's intaker watches the project folder by default; other views stay off (#102). New canvases write `watch: true` on it; a document (loaded or submitted) whose tree intaker leaves `watch` out means on, and an explicit `false` stays off.

Saved canvases cannot say which: a save writes every default, so before #115 every intaker was saved with `watch: false`. Options weighed: a schema version bump (2 -> 3) with a migration (clean, but every flow document, the journal and parallel branches carry version 2); flipping only a missing key (leaves every existing canvas off); a one-time rewrite recorded per project (chosen). On open, a project whose `project.toml` lacks `rewrites = ["tree-watch"]` turns its tree intaker on, in the same journalled load rewrite as #83's (kind `rewire`, label "turned auto-update on for the file tree"; not an undo step, the history before it dropped), then records the marker. `Project.init` writes the marker up front. After that the canvas's own `watch` is the user's say: switching the tree off sticks across reopens.

## Amendment 2026-10-23: ports on the header line, the summary in the port tip, dot link nodes, the root identicon (#121)

The user's UX pass (spec #119, points 6, 10, 11). It changes how decisions 3 to 5 and #83's node are drawn; the model is unchanged.

1. **Ports on the header line.** A port circle's centre sits on the line between a view's header and body (theme metric `port_inset` = 1 + `header_h`, from the frame's top edge), inputs on the left edge, the output on the right; stacked inputs go down from there. A collapsed view's ports stay on its title bar's middle (#114). Overworld cards keep the same canvas spots, so wires meet the ports in both levels.
2. **Two-line port tips** (replacing decision 4's wording; the strings follow Figma page 03, section 3). Line 1 says who and what, line 2 the filter or the targets; parts join with " · ", and no port names (`data`, `selected_files`) or ids show:
   - input: `from <name> · <what>` (what the upstream port carries: picked files, all files, picked items...), then the link's summary sentence without its closing full stop; a link with no sentence has one line;
   - two-way end: `two-way with <name> · <what>`, then the summary (kept on a second line so a two-way link's summary shows somewhere);
   - unlinked input: `no input · ask the agent to link one`;
   - output: `<what> · <type>` for each linked output port, then `→ <target names>`; nothing linked: every output port and ` · not linked`;
   - root output: `all files · <count>`; root refresh: `refresh all · rescan the project`.
3. **Link nodes are dots.** The link summary lives in the input port's tip, so a link node needs no label: in the wiring layer and the overworld it is a 9 px dot in the chain's colour with a surface ring (theme `link_dot_px`, in a `link_dot_hit` click area). Hovering grows it and names the ends: `<source> → <target> · <what>` (`↔` for two-way) over `click to open the link`. Clicking opens the link view directly. The summary popover is gone: its Delete and Ask agent to change move into the link view, beside Close; the link view's parameters stay read-only.
   A dot never leaves its wire. #90's rule becomes: the spot on the wire nearest the middle (`chains.NODE_STEPS`) whose click area clears every header, port circle, the project node and the dots placed before it; when no spot does, the middle. In the wiring layer the dots also stay inside the canvas area the user sees (the top bar is gone, #120): only spots inside count, and a wire with none inside puts its dot at the middle pulled inside.
4. **The root node is its identicon.** The project source is drawn as a port circle the node's size, the identicon seeded `root` in the neutral ring: the node is its one output, `all_files`, and its wires leave its right edge. No Specto mark and no separate output circle. Its refresh (#102) is a port-sized circle on the node's ring at its lower right (`chains.project_refresh_anchor`), clear of the wires leaving the middle of the right edge; the overworld's copy has none. `PROJECT_NODE_RECT` moves 7 px down, to (0, 7, 52, 52), so the node's centre lies on the line of the fresh file tree's input port (y 33) and the wire between them runs straight.
