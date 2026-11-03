---
status: accepted
date: 2026-10-22
---

# Views have roles: browser, viewer, player, scene

Agreed with the user on 2026-10-22, spec #103. Builds on ADR 0014 (views with typed ports, connected by links).

## Context

The boundary between view types was unclear. Is a gallery a kind of player? Should a video player have a gallery mode? Should one viewer show whatever is selected? Without a rule, each new data type invites a new hybrid view that browses and shows at once, and the agent has no principle for choosing between them.

## Decision

1. **Four view roles**, by what a view does, not by the data it shows:
   - **Browser**: shows many items of a collection and outputs a selection (file tree, gallery; the map partly).
   - **Viewer**: shows the one selected item in its native form (image, markdown, json, html, pdf).
   - **Player**: a viewer whose item has a timeline (video; image sequences later).
   - **Scene**: places many items in one space (3D, map).

   Every view type declares exactly one role (`specto_core.ports.VIEW_ROLES`; a plugin view type without one is refused). A gallery and a player are not alternatives: the gallery browses, and its selection feeds the player. `describe view:<type>`, `api.info`, the canvas schema's view `type` and the prompt bar's standing reference show the role, and the prompt bar's agent composes by role: a browser feeding a viewer or player.
2. **Galleries show thumbnails of every kind**: a video's poster frame, a pdf's first page, a 3D snapshot (or a kind icon until snapshots exist), an excerpt for markdown, json and html. No player or viewer gets a gallery mode.
3. **A context viewer, `preview`**: it renders the selected item by its kind (image, video, pdf, markdown, json, html, 3D model). It is the default for a double-click in the file tree and for a quick look from the agent. The specific viewers stay, for linked compositions that need typed ports such as time. _Amended by ADR 0023 (SPEC-32): a double-click opens a dedicated view of the file's kind; the preview is the contextual view, opened by Ctrl+double-click and the agent's quick look._
4. **Viewers and players step**: small previous and next controls (and keys) step through their input collection, so they are usable without a gallery.
5. **Image sequences become a player** (numbered frames plus a frame rate), later.

## Consequences

- The map is a scene with a selection output; it browses only partly, so "the map partly" is a note on the browser role, not a second role.
- `PLAYER_TYPES` (the views that may be the root view) is derived from the roles: the views whose role is player.
- New data kinds get a thumbnail in the gallery and a renderer in `preview`, not a new hybrid view type. A new view type is added only for a new role or for typed ports a viewer needs.
- The glossary's "role" now has three senses; see its flagged ambiguities.

## Considered options

- A gallery mode inside the video player (and players for other kinds): rejected; it duplicates browsing in every viewer.
- One generic viewer only: rejected; linked compositions need typed ports (time on a player, camera on a scene) that a generic viewer cannot declare for every kind.
- Roles by data type (an image family, a video family): rejected; the confusion was about what a view does, which a data type does not settle.
