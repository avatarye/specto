---
status: accepted
date: 2026-10-12
---

# Readers split into locators and worker-process decoders over shared memory

## Context

`DESIGN.md` had one "reader" per format with `can_handle(path)` and a priority. That mixes a cheap question (what is this file, which data kind) with an expensive one (decode its pixels or points), and some views (video, USD stages, ovrtx scenes) never want Specto to decode at all; they want a reference. Decoding on the GUI thread would break ADR 0004 (review finding R6).

## Decision

A reader is two things (HLD 3.7, section 10 `kinds`):

- A **locator** is cheap and may run on the GUI thread or a scan worker; it reports a priority for a path and returns a data reference (data kind, URI, metadata). Video, USD and ovrtx stop here.
- A **decoder** runs in a worker process and returns a typed buffer (a numpy array with dtype and shape, or an Arrow table) in shared memory, adopted zero-copy by the GUI thread and passed by pointer to native items.

The data-kind list is the contract between readers and views. Locator and decoder manifests are readable without importing reader code, so intake type aliases and `describe kind:` work cheaply.

## Consequences

- Assigning a collection to a view runs locators per item and yields a conversion report; items with no usable kind are listed as skipped, never dropped.
- Worker processes use `spawn` and never import Qt; a crashing decoder kills one job, not the app (HLD 4.3).
- Results returned from workers must be picklable or a shared-memory buffer spec.
- Web views cannot take shared memory; they receive binary over a custom URL scheme with range requests and a hard cap (10 M points, 2 GB).

## Considered options

- **One reader per format (QuickLook-style `can_handle` plus decode).** Rejected: conflates cheap and expensive work and forces decode where a reference is wanted.
- **Decoding in threads inside the app process.** Rejected for Python (GIL); a C++ decode thread pool in `_spectoqt` is the spike S3 fallback.
