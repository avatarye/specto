# Specto

Specto is a desktop viewer for agent-produced data in which a user and an external agent build the same viewer together, on one canvas, through one set of commands. This glossary is the language for that shared surface: what data comes in, how it is shown, how views are tied together, and how changes are made, reviewed and undone.

Sources: `docs/review/hld.md` (authoritative), `DESIGN.md` (background), `docs/contracts.md`, and the code under `packages/`. Where they disagree, see [Flagged ambiguities](#flagged-ambiguities).

## Language

### Workspace

**Project**:
A data folder opened in Specto; it has exactly one canvas. Opening the folder is the only setup a user does.
_Avoid_: workspace, repo

**Canvas**:
The one unbounded 2D surface of a project, on which its views are arranged; it starts with the file tree.
_Avoid_: session, layout, board, tab, workbench

**Canvas document**:
The JSON that completely describes a project's canvas (its intakers, views, links and layout); the canvas is driven entirely by it, and it is what agents write.
_Avoid_: session document, session file, scene (that is a view role), composition

**Object**:
Anything that lives in a canvas document and can be addressed: a view, collection, group, window frame, link, channel value, region or space.
_Avoid_: entity, node, element

**Id**:
The short, permanent handle core gives an object when it is created; never chosen by a user or agent, never reused. A view's id is its view name (`kobi`); a link's is `l<n>`. The file tree has a generated name like every view (#87); only the project source keeps a fixed id, `project`.
_Avoid_: handle, key

**View name**:
The short pronounceable name that is a view's id (`kobi`, `tamu`): two syllables handed out by the app when the view is added, unique on the canvas, never changed. One handle for the user, the agent and the document; the header shows it first, and the agent calls views by it.
_Avoid_: id (when talking to the user), handle, nickname, label (that is the summary)

**Instance name**:
The five-letter pronounceable name of one running Specto (`rasel`, CVCVC; view names have four letters, so the two never meet), stable per project on this machine. It is the address fleet agents message the window's agent by (`specto message rasel ...`); the status bar shows it first, in the agent colour, and the panel header, the window title and the details popover show it too. `specto instances` lists the running ones (#187).
_Avoid_: instance id, window name, session name

**Peer request**:
A message from another agent of the user's fleet to this Specto's agent (the endpoint's `message {from, text, reply_to?}`): a peer row in the agent thread that runs as a turn labelled with its sender. A request, never the user's approval; the user's Peer requests setting runs it (auto), asks first (ask) or refuses it (off).
_Avoid_: peer command, remote prompt

**Identicon**:
A view's small 5x5 horizontally mirrored pattern generated from its name and coloured by its link chain, so a view is recognised at a glance and related views read as a group. Ports show identicons: an input port the upstream view's, the output port the view's own.
_Avoid_: avatar, icon, badge, thumbnail

**Label**:
A very brief summary of an object written by the agent (a few words: "cam-a frames"); shown after a view's name in its header. Agents address views by name and describe them by label.
_Avoid_: title, name, description

**Address**:
The path that names an object or one of its properties, always including the canvas (`canvas:c7/view:v3.camera`); there is no "current canvas".
_Avoid_: path, selector, ref

### Intake and data

**Intake**:
The step that turns "where the data is" into a collection: find files, screen them, key them, group them, sort them.
_Avoid_: import, load, ingest

**Intaker**:
A built-in kind of link whose output is a collection: it selects files arriving on its input port (sources, screener, keys, grouping, sort, watch) and feeds a view's data input.
_Avoid_: intake spec, query, filter, importer

**Intaker scope**:
What an intaker operates on: the files the user picks (`selection`, off the file tree's selected files) or fixed sources (`pinned`, off the project source's all files). Required on every intaker but the file tree's own; the agent says which in the link summary.
_Avoid_: mode, binding mode, live/static

**Selection intaker**:
An intaker with scope `selection`: fed from the file tree's selected files, so its view shows whatever the user picks (a folder pick expands to its files), screened and keyed by the intaker; its sources stay `.` unless they narrow the picks. The default for a view placed next to the file tree. While nothing is picked the view shows "Pick files in the file tree".
_Avoid_: dynamic intaker, live intaker, filter view

**Pinned intaker**:
An intaker with scope `pinned`: fed from the project source's all files, so its view always shows the sources named in the intaker whatever the user picks. Only for data the user asked for by name ("always show the 02 run").
_Avoid_: fixed intaker, static intaker, locked (that is a lock)

**Auto-update**:
Whether a view fed by an intaker follows its files on its own: the switch in the view's header sets the intaker's `watch`, so files arriving, leaving or changing in its sources resolve in the background. Off by default except the file tree, which follows the project folder unless its intaker says `watch: false` (#115); the agent turns it on for folders an agent writes into. While it is off the status bar says "auto-update off · refresh" (#102).
_Avoid_: live mode, sync, watch mode (in user-facing text)

**Refresh**:
One manual rescan, for views whose auto-update is off: the view header's refresh rescans the intakers feeding that view, the project node's rescans every link off the project source. Not an undo step; only what changed is delivered, so nothing downstream of an unchanged result re-runs (#102).
_Avoid_: reload, rescan (in user-facing text)

**Source**:
One place an intaker looks for files: a file, a folder, several of either, or a glob, with a depth. A pinned intaker's sources are the data its view shows; a selection intaker's sources only narrow the picks.
_Avoid_: root, input (and see the flagged "source" overload)

**Screener**:
The ordered include and exclude rules of an intaker that decide which found files enter the collection.
_Avoid_: filter, matcher, gitignore

**Type alias**:
A shorthand screener rule naming a family of files by what they contain (`@images`, `@json`, `@pointclouds`).
_Avoid_: file type, preset

**Key field**:
One named piece of a file's path that the intaker extracts (`run`, `frame`).
_Avoid_: capture, tag, attribute

**Key**:
The identity of an item within its collection, made from its key fields; items in different collections with equal keys are "the same item" for linking.
_Avoid_: id, name, index

**Item**:
One entry of a collection: the one or more files that share a key (`00042.jpg` plus `00042.json`).
_Avoid_: file, record, sample, entry

**Part**:
One file of an item, distinguished from its siblings by its extension.
_Avoid_: role, sidecar (for the general case), component

**Collection**:
An ordered, keyed list of items produced by an intaker; any collection can be shown by any view able to show its items.
_Avoid_: dataset, intaker, folder, layer

**Generation**:
A collection's change counter, bumped every time a rescan or a watched arrival changes its items.
_Avoid_: version, revision

**Data kind**:
A category of content that a reader can produce and a view can accept (`image2d`, `video_ref`, `pointcloud`, `geojson`, `markdown`, ...).
_Avoid_: type, format, dataset type, MIME type

**Reader**:
The umbrella for the two halves that make an item showable: a locator and a decoder. Name the half you mean.
_Avoid_: loader, importer, parser

**Locator**:
The cheap half of a reader: it decides whether it can handle a file and says which data kind the item is and where its data is.
_Avoid_: reader, detector, sniffer

**Decoder**:
The expensive half of a reader: it turns a located item into pixels, points, text or rows on request, away from the GUI.
_Avoid_: reader, loader, codec

**Binding**:
Assigning a collection to a view; the view then shows every item it can and reports the rest.
_Avoid_: attach, feed, source (as a verb)

**Conversion report**:
The account of what a binding, dry run or recipe apply did: what bound, what was skipped and why, which quota would be hit, which effects would run.
_Avoid_: log, warnings, result

### Views and the canvas

**View**:
One live display of data or controls on the canvas (a gallery, a JSON inspector, a map, a video player), with its own state.
_Avoid_: widget, page, pane, viewer (that is a view role), window

**View type**:
The kind of a view, with the data kinds it accepts, the ports it declares and its one view role: `file_tree`, `gallery` and `scratch` (browser); `image`, `markdown`, `json`, `html`, `pdf`, `preview` and `text` (viewer); `video` and `usd` (player); `3d` and `map` (scene).
_Avoid_: view class, widget type, page type

**View role**:
What a view type does, not the data it shows: browser, viewer, player or scene (ADR 0020). Every view type declares exactly one; `describe view:<type>` and the canvas schema show it. A browser's selection feeds a viewer or player.
_Avoid_: role (bare; see the flagged "role" overload), view category, view family

**View controls**:
The icon buttons on a view's chrome: in its header the auto-update eye at the left end, before the name, then refresh, lock, float, collapse, maximise and close on the right; in its footer the step controls and the view type's own tools; a side panel's collapse. Always visible, at rest as much as on hover or selection (the user's decision of 2026-10-24, #158, replacing the UX pass's "state shows, actions hide"); a control that is on wears the accent fill. Status indicators keep their own rule: the health dot shows only when the view is not ok.
_Avoid_: toolbar, hover controls

**Browser**:
A view role: shows many items of a collection and outputs a selection (the file tree, the gallery, the scratch pad; the map partly). A gallery shows a thumbnail for every kind of item; it browses and never plays.
_Avoid_: list view, picker, file browser (that is the file tree)

**Viewer**:
A view role: shows the one selected item of its input collection in its native form (image, markdown, json, html, pdf, preview), with previous and next controls to step through the collection. A viewer has no gallery mode.
_Avoid_: view (for the role), inspector, reader (that is the locator and decoder)

**Scene**:
A view role: places many items in one space (3D, map).
_Avoid_: canvas, viewport, world, canvas document

**Preview**:
The context viewer: a view type with the viewer role that renders the selected item by its kind (image, video, pdf, markdown, json, html, 3D model) with that view type's own renderer, and any other file as a card with its name and size. On a selection it is the contextual view: what Ctrl+double-click in the file tree opens and the agent's quick look; the specific viewers stay for linked compositions that need typed ports such as time, and for dedicated views.
_Avoid_: quick look (for the view), generic viewer, inspector

**Dedicated view**:
A view opened for one file (a double-click on it in the file tree, the context menu's "Open in its own view", the agent's `canvas.open_file` in dedicated mode): a view of the type that shows the file's kind, fed that one file by a pinned link from the project source, so later picks never change it; labelled `<name> · <folder>`. A file has at most one: opening it again reveals that view. A file no view type shows opens a text view saying "Not supported".
_Avoid_: pinned view (a pinned intaker is the mechanism, not the view), own view (in prose), fixed preview

**Contextual view**:
The preview while it follows a selection (the file tree's picks, a gallery's pick): what Ctrl+double-click in the file tree opens or re-points, and what the agent's quick look reuses. Its header and its overworld card say "Contextual · follows selection". A preview the agent pinned to fixed sources is not one.
_Avoid_: quick look view, live preview, floating preview (that is a floating view)

**Text view**:
A view type (`text`, viewer role) showing one short text the canvas document carries; the "Not supported: <name> (<ext>)" view an unsupported file opens in. It accepts no data kind.
_Avoid_: note (that is a scratch pad page), message view, placeholder

**Scratch pad**:
The project's one place to keep things while looking (#184): a view type (`scratch`, browser role, at most one per canvas) of named pages (tabs), each page one markdown note `.specto/scratch/<page>.md`, rendered like the markdown view (outline, zoom) and editable in place (a rendered / source toggle, autosaved). What the user drops or pastes there from any view or another app is appended as markdown: text as text, pictures as png files in `<page>.assets/` embedded with `![...]()`, files and prims as links relative to the page, an item from a view as a short quote naming its source; the agent reads and edits the pages with its scratch tools. `pages.json` keeps the order, the names and the active page. It needs no input link (a source of its own, reachable like the project source); the page it shows (`params.page`, else the active page) goes out on `items` (the note, then the files it links) and a clicked file link on `selection`. Every change to a page's text or to the pages is an undo step on the pad's own stack, which shares Ctrl+Z with the canvas in time order; switching pages is navigation. The project node's double-click opens it.
_Avoid_: clipboard, notes view, shelf, parking (the retired parked shelf)

**Controls view**:
A built-in view type that shows a declared form of sliders, toggles, pickers and buttons bound to channels, parameters or commands.
_Avoid_: control page, form, settings page

**Web view**:
A view whose content runs in an embedded browser engine (JSON, markdown, map, 3D) under a capability list; its page has its own origin (`specto://<view id>.page`).
_Avoid_: web view, web pane, page (for the view itself)

**File tree**:
The view every canvas starts with, right of the project source: its intaker comes from the project source's all files, and its one output port, selected files, feeds the selection intakers of other views. All files come from the project source, not from the file tree. It lists one folder at a time, on expand, on its own worker (never a scan of the whole project), starts collapsed but for a chain of single-child folders from the root, resolves a folder pick to every file under it when it is picked, and follows the folders it has loaded while its intaker watches (ADR 0022).
_Avoid_: file browser, explorer, files page

**Prompt bar**:
The in-app box where the user asks the project's agent to change the canvas: since #162 the input at the foot of the **agent panel** (multi-line markdown, `/` completes the project's commands).
_Avoid_: chat, command bar, palette

**Agent icon**:
The S character (Specto's mark, #170) on a tile floating at the window's bottom left, as far from the left edge as from the bottom, above the status bar, that stands for the project's agent and shows its state with its eyes: idle (they blink), thinking (they look around), working (they scan), needs you (wide eyes in agent colours: an answer waits unread; it shakes, nothing opens by itself) or error (X eyes: the agent is unavailable or its turn errored). Resting the pointer on it opens the agent panel; so does Ctrl+K.
_Avoid_: avatar, assistant button

**Agent panel**:
The panel above the agent icon holding the **thread** and the prompt bar; it stays open while the pointer, text, focus, a selection drag or its pin holds it, and Esc closes it keeping the draft. Resizable; its size and pin are remembered per project.
_Avoid_: chat window, sidebar

**Thread**:
The whole conversation with the project's agent in the agent panel: each request, each reply (markdown), each prompt turn's turn row (its label, Undo, diff), saved skills, saved slash commands and data files (each with Open and Undo), and a divider where a session began. Kept in `.specto/agent-thread.jsonl`; older turns load as the user scrolls back.
_Avoid_: chat log, history (that is the canvas's undo history)

**Prompt turn**:
One request typed in the prompt bar and the agent's work on it until it replies: every submit the agent makes in that time, folded into one undo step when the turn ends (ADR 0013 amendment).
_Avoid_: turn (bare), conversation turn, round

**Reply strip**:
The one line that says what a prompt turn changed, with Undo for the whole turn and the detailed diff on request; since #162 it is the turn's row in the **thread**.
_Avoid_: chat log, status line

**Context menu**:
The shell's one menu for what is under the cursor (items of a view, the view, or its background), opened by a right-click, the Menu key or Shift+F10; its entries are data each view type contributes (#154). Browsers start with Copy path and Copy relative path.
_Avoid_: tile menu, right-click menu, popup menu

**Launcher**:
An external program the user configured, in the machine's or the project's `launchers.toml`, to open selected files (an isolated Blender build the system does not know); offered in the context menu's "Open with" and to the agent, beside the system default (refused for programs and scripts) and Show in Explorer (#186).
_Avoid_: opener, external editor, app association

**Group**:
(M2 term, retired in v2: views snap instead.) A split-and-tab arrangement of views that moved as one block on the canvas.
_Avoid_: dock area, layout, page group, container

**Window frame**:
A visual guide on the canvas showing where the app window is and how big: its size follows a window resize (one undo step) and its origin follows the user's pan as navigation (no undo step), so it is always the rectangle that renders and that quotas count. View borders snap to it. Specto has one app window and never opens more.
_Avoid_: frame (bare), OS window, desktop, viewport

**Normal view**:
The canvas at 1:1, pixel-perfect: views on screen render live, views panned off screen stop rendering, and views may sit anywhere (free-form).
_Avoid_: live view, app view, main window, workbench

**Snapping**:
Moving or resizing a view pulls its border flush against a neighbouring view or the window frame, removing gaps; Specto does not dock or tile views.
_Avoid_: docking, docked mode, tiling, grid

**Float layer**:
The layer above the pannable canvas, fixed in the window: panning moves the canvas under it, its views never pause and always count against the quotas, and the overworld shows them in a "floating" strip along its top (ADR 0021).
_Avoid_: overlay, HUD, top layer, pinned layer

**Floating view**:
A view on the float layer: it keeps its offset from the nearest window corner (so it stays put when the window resizes), drags only within the window and snaps to the window edges and other floating views, not to canvas views. The agent may float and unfloat views, but may not move or resize one the user floated.
_Avoid_: pinned view, sticky view, docked view

**Float**:
The view header's gesture (and the agent's submit) that puts a view on the float layer where it is on screen; **Unfloat** drops it back onto the canvas at the canvas point under it. Each is one undo step.
_Avoid_: pin (a pinned intaker has fixed sources), dock, stick

**Collapsed view**:
A view shown as its title bar alone, at the same width (the header's collapse button, or the agent's `layout.collapsed`; one undo step either way). Its content is paused and costs no quota, its ports stay on the title bar and its links keep delivering, so expanding shows the latest data at the stored height. Snapping, the marquee and the overworld card use the title bar's rect (ADR 0021 amendment).
_Avoid_: minimised, folded, hidden, parked (a parked view is not shown at all)

**Overworld**:
The paused, zoomed-out level of the canvas where the user moves and resizes views freely; nothing renders there, and positions resolve, snapped, into the normal view.
_Avoid_: layout view, map, zoomed-out mode, minimap

**Tile floor**:
The canvas's background, under the views in the normal view and the overworld: square tiles on an 8 px grid fixed to the screen (it changes only when the window does) that light up as pulses (the agent thinking, needing the user or done at the agent icon; a new view; a far view's edge pulses and chip) and as the selection tie hugging each selected view; it dims under the wiring layer and redraws only while a pulse runs (#191, #197).
_Avoid_: grid, dot grid, background pattern

**Card**:
A view as shown in the overworld: label, type, bound collection, link ports, provenance and the age of its last snapshot.
_Avoid_: tile, thumbnail, node

**Wire**:
A link as drawn between ports in the wiring layer and the overworld; dragging a wire creates a link.
_Avoid_: edge, connection, cable

**Link port**:
The point on a card where a view offers a channel for wiring.
_Avoid_: socket, pin, handle

**Parked**:
Said of a view that belongs to no group; it is kept on the canvas but neither shown nor rendered.
_Avoid_: hidden, closed, stashed

**Off-screen**:
Said of a view whose group lies outside every on-screen window frame; it is kept, paused and not rendered.
_Avoid_: parked (see flagged ambiguities), hidden

**Paused**:
The state of a view that keeps its settings but does no per-frame work and gives back GPU memory where it can; every view in the overworld is paused.
_Avoid_: suspended, frozen, sleeping

**Shelf**:
(M2 term, retired in v2.) The normal view's list of views it could not show (parked or off-screen).
_Avoid_: tray, bin, drawer

**Region**:
A labelled area of the canvas for organising groups and parked views (cut from the plan on 2026-10-12; still addressable).
_Avoid_: zone, area, section

### Linking views

**Channel**:
A named, typed kind of value that views publish and receive: `time`, `hover_time`, `item`, `pan_zoom2d`, `camera3d`, `map_view`, `selection`, `cursor`, `scalar`, `scroll`.
_Avoid_: signal, topic, event, bus

**Link**:
An edge on the canvas that takes a value from one view's output port, passes it through the link's mapping (a built-in or Python code) and feeds another view's input port; seen from the view it feeds, a link is the filter on its input port (one link per input port, so hovering the port shows the link's summary). Links never form cycles.
_Avoid_: pair, sync, binding, connection, wire (that is its drawing), pipe

**Port**:
A typed input or output of a view, such as all files, selected files, file contents (for example polygons from GeoJSON files), item, or time. Links connect an output port to an input port. Each is drawn as a port circle centred on the line between the view's header and body, inputs on the left edge, the output on the right; hovering one shows a two-line tip (an input: the upstream view and what it carries, then the link summary; the output: what it emits, then where it goes).
_Avoid_: socket, slot, pin, channel (for the port itself)

**Port capsule**:
A solid capsule in the link chain's colour joining the two port circles of one link (the source's output and the input it feeds, both showing the source's identicon) when they come within the join distance on screen, the identicon at its midpoint; it holds while a view moves until the circles pass the larger break distance, then splits back into the two circles. Hovering it shows the link's input port tip; in the wiring layer it stands for the link's wire and opens the link view.
_Avoid_: bridge, merged port

**Port type**:
The kind of value a port carries: files, collection, item, selection, time, content. Ports are declared by each view type, never in the canvas document; all files and selected files are two ports of the files type.
_Avoid_: channel type, data type (for ports)

**Link kind**:
What a link does to its value: intaker, follow (by key, optionally matching on one key field such as frame), read contents, time map, or Python.
_Avoid_: link mode, mapping type

**Per-pick link**:
A Python link with `resolves_to: value`: its approved code runs, out of process, for each new value of its source (debounced; only the latest pick's result lands) and returns the target port's value, converted to that port's type. The way to express a mapping no built-in kind can, such as a recording picked in one file tree selecting its annotation folder in another.
_Avoid_: live link, dynamic link

**Project source**:
The project folder as the one source of the canvas's dataflow: drawn as its identicon (seeded "root") in a ring, like a port circle, with no card body and a refresh circle on its right side, fixed at the canvas origin left of the file tree. Its one output port, all files, feeds the file tree's intaker and every pinned intaker; every view the agent submits must be reachable from it by links (a view the user unlinks shows "not connected" until relinked). Nobody moves, resizes, deletes or locks it; it is not a view (ADR 0018 amendment).
_Avoid_: root view (that is a player), project view, root node

**Link chain**:
A set of views linked together; its views share one colour, carried by their identicons in the normal view and by clear wires in the wiring layer and the overworld.
_Avoid_: lineage, group, cluster

**Wiring layer**:
The overlay over the normal view, toggled with the backtick/tilde key, in which views are dimmed and ports, links, curves and link nodes are drawn; the normal view itself draws no curves.
_Avoid_: link mode, wire view, debug overlay

**Link node**:
The small dot on a link, drawn on its wire in the wiring layer and the overworld, that opens the link view. It carries no label (hovering names its ends): the link summary is the tip of the input port the link filters.
_Avoid_: link handle, wire node, junction

**Link summary**:
A plain sentence or small diagram, written by the agent, saying what a link does (for example "selecting an image shows the video recorded at that moment"); what the user reads instead of the link's code or parameters. It shows in the tip of the input port the link filters and at the top of the link view.
_Avoid_: link description, rule text, code view

**Link view**:
A link opened from its link node: a special view showing the link's summary, code and parameters (read-only), with Delete and Ask agent to change.
_Avoid_: code view, link editor

**Resolve**:
Working out everything a link needs (parsing files, scanning, building its lookups) when its logic or its inputs change, so that rendering in the normal view only reads ready results.
_Avoid_: compile, evaluate, run (for this step)

**Selection**:
The output a view gives for what the user picked: the selected items and, among them, the last one selected, as one value.
_Avoid_: selected item (alone), focus

**Selected views**:
The views the user has marked on the canvas: a click on a header selects one, Shift or Ctrl toggles, a left drag on the empty canvas (the marquee) selects the views it touches, the empty canvas or Esc clears. Navigation state, not an undo step; it travels in the prompt bar's canvas context so "these" in a request names them. Distinct from a view's selection output (above), which is about items inside a view.
_Avoid_: focused views, highlighted views

**Global clock**:
The shared time base that lets data recorded at different frame rates play back together; it does nothing else and never drives playback.
_Avoid_: master clock, timebase

**Player**:
A view role: a viewer whose item has a playback timeline (video; a USD stage, whose timecodes are its timeline; image sequences, as numbered frames plus a frame rate, later). It plays the item selected in a browser and steps through its input collection; it has no gallery mode. Playback time is always driven by a player, and the player other views follow is the root view.
_Avoid_: transport, deck, gallery (that is a browser)

**Root view**:
The player whose playback timeline other views follow for time.
_Avoid_: master view, leader

**Submit**:
The agent's signal that a manipulation of the canvas document is finished: a whole document or an **edit** of the live one; the resulting document is validated as a whole and applied as one undo step. A submit that changes the canvas ends the agent's prompt turn.
_Avoid_: save, commit (for the agent's signal), done

**Edit (submit)**:
A submit that names only what changes, in the canvas document's own shape (a JSON Merge Patch over the live document): objects merge, other values replace, `null` removes, anything left out is kept. The preferred form for the agent; the app merges it and then submits the result as a whole.
_Avoid_: patch (that is the stored change), delta, diff

**Turn-start conflict**:
A submit during a prompt turn that would change a view or link the user changed after the turn began; it is refused with the object's live entry and is not counted as a failed attempt.
_Avoid_: merge conflict, race, stale write

**Two-way link**:
A link that syncs both views in both directions; only some port types allow it (time, file selection).
_Avoid_: bidirectional binding, mirror

**Channel value**:
The current value of a channel on one link, with its revision and the view it came from.
_Avoid_: chan (outside addresses), state, message

**Link mode**:
`two-way` (any member publishes) or `follow` (only the first member publishes, the rest follow).
_Avoid_: direction, master/slave

**Link transform**:
The conversion a link applies between members, such as a time offset between a video and a gallery or a change of space.
_Avoid_: mapping, adapter, offset (for the general case)

**Origin**:
Who set a channel value or made a change: a view, the user, an agent session or a recipe.
_Avoid_: author, sender, source

**Tick**:
The once-per-rendered-frame moment in which every changed channel value is delivered along its links, latest value only.
_Avoid_: frame (bare), update, cycle

**Timeline**:
A named clock that `time` values are measured on (stamps, video time, item index); each collection declares its timeline and offset.
_Avoid_: clock (as the term), track, time base

**Space**:
A coordinate frame that collections and views declare: a map projection, a local east-north-up origin, or a sensor pose over time.
_Avoid_: frame, coordinate frame, CRS (for the general case)

**Frame graph**:
The graph of spaces and the poses between them, which link transforms use to carry cameras, cursors and selections across views.
_Avoid_: transform tree, scene graph

### Changes, review and undo

**Command**:
A named operation with typed arguments and result; every change any human or agent makes is a command.
_Avoid_: action, operator, API call, RPC

**Transaction**:
A group of commands that applies, undoes, journals and is reviewed as one unit; a command outside one is its own transaction.
_Avoid_: batch, turn, macro, operation

**Patch**:
The exact change a transaction made to the canvas document, together with its inverse; what the GUI reacts to and what the diff shows.
_Avoid_: delta, diff (for the change itself), event

**Effect**:
Something a command does outside the canvas document (opens a window, writes a file, starts a watch) that undo cannot take back.
_Avoid_: side effect (as the term), external action

**Irreversible transaction**:
A transaction the caller allowed to contain effects; undo reverts its document change and lists what it could not revert.
_Avoid_: unsafe, destructive (that is a different command flag)

**Dry run**:
Committing a transaction against a copy to get its patch and conversion report without anything landing.
_Avoid_: preview, simulation, proposal

**Changeset**:
A committed transaction as presented for review: highlighted on headers and cards, revertable per change or as a whole, and diffable.
_Avoid_: proposal, suggestion, diff, turn

**Changeset bar**:
The strip that lists recent changesets with fly-to, revert and diff.
_Avoid_: history page, review bar

**Undo stack**:
The single linear history of undoable transactions on a canvas; an older transaction is undone only after newer ones.
_Avoid_: history (bare), timeline

**Journal**:
The append-only record of every transaction on a canvas, replayable from a snapshot.
_Avoid_: log, history, audit trail

**Provenance**:
The record on each object of who last made it or changed it (user, agent session or recipe) and in which transaction.
_Avoid_: author, owner, blame

**Lock**:
A mark on a view, group or window frame that makes any move, resize, relink or delete fail with the lock holder and the unlock command.
_Avoid_: pin, freeze, protect

### Agents and clients

**Agent session**:
One external agent's identity while it drives Specto; its changes carry it as origin and provenance.
_Avoid_: session (bare), agent, client

**Connection**:
One client attached to a running Specto app; it owns the transactions it began, which abort if it goes away.
_Avoid_: session, socket, client

**Snap**:
A structured snapshot of a view or the canvas (document entry, links in and out with their resolve state, health, on-screen rect, host state; on request a PNG and the layout as text) that an agent checks its work against; one of the six agent tools.
_Avoid_: screenshot, capture (that is a recipe term)

**Outline**:
The cheap tree view of a canvas's state that an agent reads first.
_Avoid_: summary, tree, state dump

**Walk-through**:
The agent's animated tour of how a view's data reaches it from the project source, asked for in the prompt bar ("walk me through pola"): its **hops** (one link each) are planned along the links by the fork rule (at a **fork**, a view with several inputs, the walk arrives along the longest chain, then takes each other input from the nearest view already walked), the agent captions every hop in one pass, then it plays with step, play/pause, replay and exit, each step staying long enough to read its caption at the user's **walk speed** (slow, normal or fast: a user setting the agent may set when asked, never in the document; #183). It only shows the canvas; nothing changes (#174).
_Avoid_: tour, demo, presentation, trace

**Vocabulary**:
The self-description of everything an agent can compose with: commands, view types, data kinds, channels, theme tokens.
_Avoid_: schema, API docs, catalogue

### Runtime

**Job**:
Work that may take longer than a moment (scan, decode, thumbnails, recipe apply), started by a command and tracked until done, failed or cancelled.
_Avoid_: task, process, background operation

**Health**:
A view's current condition (`ok`, `loading`, `paused`, `error`) with a message, visible to the user and the agent.
_Avoid_: status, state

**Quota**:
A per-canvas ceiling on costly live views (web views, 3D viewports, resident points, video decoders, RTX views), enforced when a view would become live.
_Avoid_: limit, budget (for the enforced ceiling)

**Frame source**:
An external renderer (ovrtx, Blender, Storm as fallback) that sends frames into a view and takes camera and input back.
_Avoid_: frame (bare), viewport, renderer plugin

### Recipes and trust

**Recipe**:
A saved, reusable viewer setup that rebuilds itself on any matching data through roles.
_Avoid_: template, preset, layout, macro

**Role**:
A typed slot in a recipe that a collection is bound to when the recipe is applied (`lidar: pointcloud`).
_Avoid_: parameter, slot, input (and see the flagged "role" overload)

**Capture**:
Turning an explicitly scoped part of a canvas into a recipe, each collection becoming a role.
_Avoid_: export, snapshot, save-as

**Apply**:
Instantiating a recipe on a canvas by binding sources to its roles; it lands as one group.
_Avoid_: run, load, instantiate

**Golden**:
A stored expected state of a recipe's views, used to check the recipe still works; images are advisory.
_Avoid_: baseline, fixture, reference image

**Trust**:
The user's approval of one exact content (by hash) to run code, regardless of who wrote it. Given only by the user's click in the window (a view's Approve, an approval card); no command, tool or peer gives it.
_Avoid_: signing, allowlist, permission

**Approval card**:
A row in the agent thread that a `link.approve` request from the endpoint opens: it names the requester (verified or claimed), the file and the link, and waits for the user's Approve or Dismiss. The request returns pending; the card is the only way it gets approved.
_Avoid_: approval prompt, consent dialog

**Restricted Mode**:
How untrusted content opens: layout and data load, code does not, and the canvas says so.
_Avoid_: safe mode, sandbox

**Capability**:
One named grant a web view holds (read a collection, publish a channel, call a command, load map tiles); anything not granted is refused.
_Avoid_: permission, scope, entitlement

## Relationships

- A **Project** holds many **Canvases**, **Intake specs** and **Recipes**; each **Canvas** has exactly one **Canvas document**.
- An **Intake spec** has one or more **Sources** and one **Screener**, and produces exactly one **Collection**.
- A **Collection** is an ordered list of **Items**; each **Item** has one **Key** and one or more **Parts**.
- A **Locator** gives each **Part** a **Data kind**; a **View type** accepts a set of **Data kinds**; **Binding** a **Collection** to a **View** yields a **Conversion report**.
- A **View** is bound to at most one **Collection** (a map may layer more); a **Collection** can be bound to many **Views**.
- Every **View type** has exactly one **View role**; a **Browser**'s **Selection** feeds a **Viewer** or **Player**, and a **Scene** places many **Items** in one **Space**.
- A **Group** contains one or more **Views**; a **View** is in at most one **Group**; a **View** in no **Group** is **Parked**.
- A **Window frame** contains zero or more **Groups**; a **Group** outside every on-screen **Window frame** is **Off-screen**; both kinds of unseen **Views** are **Paused** and listed on the **Shelf**.
- A **Link** has one **Channel**, two or more member **Views**, one **Link mode**, an optional **Link transform** and one **Channel value**.
- The `item` **Channel** carries a **Key**, so **Views** on different **Collections** follow each other wherever their **Keys** are equal.
- A **Collection** declares a **Timeline** (and, from M3, a **Space**); **Link transforms** convert between **Timelines** and between **Spaces** along the **Frame graph**.
- Every change is a **Command**; every **Command** runs in exactly one **Transaction**; a committed **Transaction** has one **Patch**, appears as one **Changeset**, one **Journal** entry, and (if undoable) one step on the **Undo stack**.
- A **Connection** owns the **Transactions** it began; an **Agent session** speaks through a **Connection** and is the **Origin** stamped into **Provenance**.
- A **Lock** sits on a **View**, **Group** or **Window frame** and is held by the user or an **Agent session**.
- Any **View**, canvas or floating, may be a **Collapsed view**: **Paused** like an **Off-screen** one, but still drawn (its title bar) and still fed by its links.
- A **View** is either on the **Canvas** or a **Floating view** on the **Float layer**; a **Floating view** the user floated binds the agent like a **Lock** binds a move, but the agent may unfloat it.
- A **Recipe** has one or more **Roles**; **Capture** makes a **Recipe** from a scoped part of a **Canvas**; **Apply** binds **Sources** to **Roles** and lands one **Group**.
- **Trust** is recorded per content hash; content without it opens in **Restricted Mode**.

## Example dialogue

> **Dev:** "The agent made two galleries and linked them. Is that one changeset?"
> **Domain expert:** "If it began a **transaction** first, yes: one **changeset**, one **undo**. Each gallery is a **view** bound to its own **collection**; the **link** is on the `item` **channel**, so clicking item `00042` in one selects the item with the same **key** in the other."
> **Dev:** "And if I drag one gallery out of the **window frame**?"
> **Domain expert:** "Its **group** is now **off-screen**, so the view is **paused** and shows on the **shelf**. Close it out of its group instead and it is **parked**. Either way the link and its **channel value** stay."

## Flagged ambiguities

- **View vs widget / page / pane.** `DESIGN.md` says widget, page and pane for the same thing; HLD principle 6 picks **view**. Resolved (2026-10-18): **view** for anything on the canvas; the shell has no side web views in v2 and "web view" became **web view** everywhere (class `WebView`, quota `web_views`, origin suffix `.page`).
- **Session vs canvas.** `DESIGN.md` calls the canvas document the "session document"; HLD 3.2 says canvas and session are synonyms; the code's `CanvasSession` (`engine.session(canvas_id)`) is the runtime state of an open canvas (undo cursor, events), not the document; and "agent session" means an agent's identity (`agent:<session>`). Resolved: **canvas** / **canvas document**; "session" only inside **agent session**.
- **Dataset vs collection.** `DESIGN.md` uses dataset (`col:frames`, "datasets in the session document", ROS topics "map to Specto datasets"); HLD and code use collection (`col`). Resolved: **collection**.
- **Intaker.** `DESIGN.md` uses "intaker" for what HLD calls an **intaker** ("an intaker finds and screens files into a collection", "drag-and-drop creates an intaker"); the code uses `intaker` for the side page (`specto_app.intaker`). Resolved: **intaker** for the description, **Intaker** only for the side page.
- **Frame, five ways.** (1) **window frame** (canvas rectangle bound to an OS window; addresses and commands say `frame:` / `frame.new`); (2) video or sequence frame, and the common `frame` **key field** in test data ("linked on `frame`"); (3) **frame graph** of coordinate spaces (HLD keeps the name even after renaming coordinate frames to **space**); (4) **frame source**, an external renderer; (5) a rendered frame, the unit of a **tick**. Resolved: never say "frame" bare in prose; "frame graph" could become "space graph" to finish the HLD's own rename (proposed).
- **Reader vs locator / decoder.** `DESIGN.md` has one "reader" with `can_handle` and a "reader registry"; HLD 3.7 splits it into **locator** and **decoder**; the package is still `specto-readers`. Resolved: **reader** is only the umbrella; name the half.
- **Source, three ways.** An intake **source** (where files are found); a view's `source` field in the document and `view.new --source` (the bound collection); and **frame source**. Resolved: **source** means intake source; for a view say **binding** / bound collection.
- **Role, three ways.** A recipe **role** (typed collection slot), the code's part "role" (`Item.parts: role -> path`, keyed by extension, `_role_for` in `specto_intake.keys`), and since 2026-10-22 a **view role** (browser, viewer, player, scene; ADR 0020, `VIEW_ROLES` and the `role` field of `describe view:<type>`). Resolved: **part** for the second; say **view role** for the third whenever a recipe role could be meant.
- **Parked vs off-screen.** HLD 5.1 says "groups outside every frame are parked"; `DESIGN.md` says "widgets outside every frame are parked"; contracts section 5 and `specto_core.layout` say a view in no group is **parked** and a group outside every frame is **off-screen**; the shell's `controller.parked` lists both (`where: parked | off-screen`). Resolved: the code's two-term split; HLD 5.1 is the outlier.
- **Channel vs chan.** HLD 3.2 shows one `chan` object per channel per canvas (`ch_time`); the code (contracts section 5) has one `chan` object per link (`l1.chan = ch1`, `ch1.link = l1`). Resolved: **channel** is the type, **channel value** is the per-link value (the code is right; the HLD example is stale).
- **M1 `item` channel and "linked on `frame`".** HLD M1 exit criterion 1 and `DESIGN.md` ("two galleries ... linked on `frame`") read as if a link names a key field. In code the `item` channel carries the whole **key** (`{key, col?}`) and matches by equality; the key is the key fields joined in order (`IntakeSpec.key_fields`), and is prefixed with the source index when a spec has several sources without `merge_roots`. So "linked on `frame`" only works when both collections key on `frame` alone; two collections keyed on `run` and `frame` never match across runs. Unresolved: either keys must be authored per link, or the `item` channel needs a field-level match.
- **Regions.** HLD section 10 cut labelled regions (`frames` answer), but `region` is still an object kind and address kind in `specto_core.document` / `address`. Unresolved: remove, or reinstate.
- **Turn vs transaction.** `DESIGN.md` says "every agent turn is one undoable transaction"; M2 had transactions the agent bracketed by hand. Resolved again (2026-10-18, ADR 0013 amendment): v2 has no transactions; a **submit** is the atomic change and a **prompt turn** is one undo step (its submits are squashed when it ends).
- **Proposal vs changeset.** `gui-styles.md` direction B lands agent turns as proposals, ghosted until accepted; HLD section 10 (`review`) chose apply live plus **changeset** review, with no proposed state. Resolved: **changeset**; "proposal" is reserved for a possible later experiment built on **dry run**.
- **Channel list drift.** HLD 3.8 lists `camera3d`, `map_view`, `cursor` (not yet in code); the code adds `scroll` (M2 text views), which no design doc lists.
- **Undo in `DESIGN.md`.** `DESIGN.md` describes per-command `undo()` on a `QUndoStack` with an `undoable` command flag; the HLD replaced that with patch-inverse undo. The `undoable` flag came back in M1.5 with a different meaning: "stays off the undo stack" (selection, navigation, window resizes), not "has an undo method".
- **Intaker (resolved 2026-10-17):** the user's word for the data selector won. "Intaker" now names the canvas-document entry (formerly "intake spec"); the old side page of that name is gone from the agent-first UX.
- **Project and canvas (resolved 2026-10-17):** one project has exactly one canvas; addresses still name the canvas (ADR 0010) but there is only ever one.
- **Intaker vs link (resolved 2026-10-17):** an intaker is a kind of link (one that outputs a collection); there is one wiring concept. Internally intakers evaluate as batch, cached work and other links as interactive latest-value updates.
- **View vs viewer (resolved 2026-10-17, amended 2026-10-22):** "view" is canonical for anything on the canvas; since ADR 0020 a **viewer** is one view role (shows the one selected item), so "viewer" no longer means any view.
- **Intaker scope (resolved 2026-10-18, user test 1):** "when I change the file selection the downstream views don't update": the agent had fed every view from all files with sources pinned to one run, so nothing depended on the picks. Resolved: views fed from the file tree follow the selection by default; an intaker off a file tree must say `selection` or `pinned` (ADR 0014 amendment), and the two names are the vocabulary for "follows what I pick" vs "always shows this".
- **Project source vs root (resolved 2026-10-19, #83):** the user asked for "a special root node" that holds all files; the term stays "project source" because "root view" is the player other views follow for time. The project source is now drawn and is the only source of all files.
- **Edit vs patch (resolved 2026-10-23, #98):** the spec's option was "partial submits", a JSON Merge Patch. "Patch" already names the exact stored change and its inverse (`make_patch`), which an edit is not: the stored patch is always the difference between the live and the merged document. Resolved: the agent's argument is **edits** (an **edit**), and "patch" stays the stored change.
- **Pin vs float (resolved 2026-10-22, #112):** the user asked for "a pin button that makes a view float on top"; "pin" already names an intaker with fixed sources (**pinned intaker**), so the gesture is **Float** and the layer the **float layer**.
- **Channel vs port (open):** the M1/M2 code links views on shared channels; the agent-first model connects typed ports with directed links. Whether "channel" survives as an internal term is decided with the canvas document schema.
