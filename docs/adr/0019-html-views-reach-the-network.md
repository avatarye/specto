---
status: accepted
date: 2026-10-19
---

# html views reach the network by default

Amends the offline rule (`docs/contracts.md` section 14: every web asset is vendored, the map's basemap tiles the one exception) and the html view's "no network" of #71. Decided by the user on #93.

## Context

The html view shows a user's own page: a deliverable report, a deck. Such pages build their images and maps from remote URLs (a remote image `src`, a map embed). Under #71 the view held no `net:` grant, so the request interceptor refused every remote request and the page showed its fallbacks. The user wants those pages to look as they do in a browser.

## Decision

1. **`net:web` grant.** A web view holding it may make any http(s) request from its frames, and its frames (never the main frame) may navigate to http(s). The html view holds it by default; no other view type does (the map keeps `net:tiles` only).
2. **`network` view param.** `params.network: false` drops the view's `net:` grants; true or absent keeps the type's default. It is declared in the html manifest (`defaults`, `state_schema`), so `describe view:html` and the generated types show it, and the agent sets it like any param. Params may narrow grants, never widen them.
3. **What stays.** The page's main frame stays on its `specto://<view>.page` origin; the user's document stays on the view's items origin with no WebChannel bridge; local items are still served only by `specto://`. Requests refused while the grant is off keep logging `[WARN]`.
4. **Attribution.** The interceptor charges a request to the view of its top-level page (`firstPartyUrl`, which is the view's page because the main frame never leaves it), falling back to a `specto` initiator. A remote frame's own requests (the map embed's tiles) carry a remote initiator, so the initiator alone cannot name the view.

## Consequences

Rendering a user's page now contacts the third parties it names, with the user's IP address and QtWebEngine's cookies. A page that must stay offline is set `network: false`. App assets are still vendored; the offline rule holds for everything Specto ships.
