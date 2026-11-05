---
status: accepted
date: 2026-11-05
---

# Neither side of a frame source's control channel waits on the other

Task SPEC-33. Amends the frame-source protocol of #136 (`specto_app/frames/control.py`).

## Context

A frame source is a process the host drives over JSON lines on its stdin and stdout. Until SPEC-33 both sides were synchronous. The host wrote each request into the source's stdin on the caller's thread, the GUI thread for camera moves. The source's control thread read a request, handed it to the render thread when the render thread owned the work (a pick, a view mode switch), and waited for the answer before it read the next line.

A slow command therefore froze the app. Switching a usd view to collision on a stage with dense colliders made the render thread build convex hulls for minutes. The control thread waited on it, so nobody read stdin. The orbit's camera moves filled the pipe, and the GUI thread blocked on its next write.

## Decision

1. **The host never writes on the caller's thread.** `send` and `call` queue the request and return; a writer thread writes the queue in order. A source that stops reading costs a queue, not a frozen GUI.
2. **State updates collapse to the newest while queued.** `set_camera`, `set_time` and `resize` set one piece of the source's state each, the newest winning. A queued one not yet written gives way to a newer one of its kind; they touch separate state, so they may pass each other, and any other request keeps its place between them. A `cut` among the collapsed camera moves survives (the newest goes out as a cut). The id of a request given way is never answered: the host only sends these with `send`, which waits for no answer.
3. **The source reads on while a request is in hand.** A handler that cannot answer at once returns a `Future`; the answer goes out when it resolves, from whatever thread resolved it. Answers may therefore come out of request order: the host matches them by id, and must not assume order.
4. **A source may answer superseded requests with the latest result.** The usd source collapses view modes asked while one waits: every waiting request is answered with the switch that took place. A view mode asked before a `load` is answered with an error, since every load starts lit.
5. **What the picture shows is also an event.** The usd source announces each view mode switch as `view_mode {mode, note}`, so the host can follow the picture even after it stopped waiting for an answer.
6. **No answer is lost.** A result that does not serialise answers an error, and so does a source that goes away (its writer's broken pipe or its stdout's end) for every call still waiting.

## Consequences

- A host error from `send` (the source is gone) now shows up on the next send or as the `exited` event, not on the send that met the broken pipe.
- Work the render thread used to do before answering, such as building the collision overlay, belongs on a thread of its own. The render loop keeps drawing, and the work answers through the future. The usd source bounds that work, at 90 s for the overlay, under the host's 130 s wait, and stops it between colliders when a load, a newer build or the process's end makes it moot.
- Any new source method that hands work to another thread returns a future instead of blocking the control thread.

## Considered options

- Keep blocking writes and add a timeout to each: rejected; the GUI thread would still stall for the timeout on every camera move behind a slow command.
- A second pipe for camera moves: rejected; it splits the protocol for one symptom, and a slow command would still stop picks and state queries.
- Drop camera moves on the host while the source is busy: rejected; the host cannot tell busy from slow, and collapsing in the queue keeps the newest move without guessing.
