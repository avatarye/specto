---
status: accepted
date: 2026-11-03
---

# A file opens in a dedicated view; the contextual preview is a separate gesture

Task SPEC-32. Amends ADR 0020 decision 3 (the preview as the default for a double-click in the file tree).

## Context

Since #106 and #122 a double-click on a file in the file tree opened the one preview on the tree's picks: it showed the file now and every later pick. That made a quick look cheap, but a user who double-clicked a note to keep it open lost it on the next click in the tree, and nothing on the canvas said whether a view was fixed on a file or following the selection. Opening a file for keeps meant asking the agent, or wiring a pinned intaker by hand.

## Decision

1. **Two ways to open a file from the file tree, two gestures.** A double-click opens a **dedicated view**; Ctrl+double-click opens (or re-points) the **contextual view**. The context menu offers both ("Open in its own view", "Open in contextual view"), as does `canvas.open_file` through its `mode`. The command keeps `contextual` as its default: an agent's "show me this" was a quick look before SPEC-32 and stays one; a dedicated view is asked for by name.
2. **A dedicated view is an ordinary view.** Its type is the view type that shows the file's kind, read from the plugin registry (`PluginRegistry.typed_view_for`): among the registered view types that accept one of the file's kinds (in the locators' order), browsers and the context viewer excluded, the one with the narrowest `accepts` list; so a `.md` opens the markdown view rather than the json inspector, a `.geojson` the map, a `.usda` the usd view. It is fed by a **pinned** link from `project.all_files` to exactly that file (an intaker on its `data` port, or `read_contents` on the map's `features`), so later picks never change it. Its label is `<name> · <folder>`, the folder relative to the project (the project's name, or `.`, at the root).
3. **A dedicated view is recognised by its link, not by a mark.** The view dedicated to a file is any view that is not the context viewer whose file input is fed from the project source with that one file as its sole source (`gestures.dedicated_view_of`). A second double-click focuses and reveals it (selected, panned into the viewport) instead of opening another; a view the agent composed the same way counts. No `params.file`, no flag to keep in step with the link.
4. **A file no view type shows opens a `text` view** saying `Not supported: <name> (<ext>)`, titled and linked like every dedicated view, so it is recognised the same way. `text` is the smallest new view type: a viewer of one short text in `params.text`, accepting no data kind.
5. **The contextual view is the preview on a selection**, as before: a selection intaker off the tree's picks, the file picked in the tree as part of the gesture (navigation, no undo step). It says so: a "Contextual" tag and "follows selection" after its title, in the header and on its overworld card (`gestures.is_contextual`: the context viewer not fed fixed sources from the project source). A Ctrl+double-click leaves the file selected whatever the Ctrl taps before it toggled; when the preview cannot render the file's kind (a usd stage), the dedicated view opens instead, and with no view for it the `text` view; the result's `mode` says which.
6. **One undo step per gesture**, as every gesture: one submit for a new view or a re-pointed link; none for a pick or a reveal.

## Consequences

- `gestures.open_file` takes `mode` (`dedicated`, the gesture's default; `contextual`) and the plugin registry it reads kinds and view types from; `OpenedFile` gains `mode` and the `focused` action. The file tree's `open_row` takes the tap's modifiers; `FileTreeHost.open_path(path, mode)` serves the menu too.
- The glossary's **Preview** is no longer the default for a double-click; **Dedicated view** and **Contextual view** are the terms.
- `text` joins `VIEW_TYPES`, `VIEW_ROLES` (viewer) and `VIEW_PORTS` (a `data` input for its link).
- Flows that opened the preview with a double-click now Ctrl+double-click (`ctrldblclick` in the flow language).

## Considered options

- Mark the dedicated view in its params (`params.file`): rejected; the link already says it, and a mark can disagree with the link after an agent's edit.
- One table in `gestures` from kind to view type: rejected; the manifests already declare what each view accepts, and a plugin's view type should count without editing core.
- Make `canvas.open_file` default to `dedicated` to match the gesture: rejected for now; agents' quick looks keep working unchanged, and the mode is explicit either way.
- Reuse the preview's card for unsupported files: rejected; the preview is the contextual view by definition, and a card there would be re-pointed by the next pick.
