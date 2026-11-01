---
status: accepted
date: 2026-10-31
---

# The file tree lists folders on demand and searches with the machine's index

Issues #231 and #232. Amends ADR 0014 (the file tree is a view whose intaker is the project folder) for how the tree gets its data.

## Context

The file tree read every file under the project before it showed a row (`project.all_files` is a full recursive scan, and the tree built the whole tree from the resolved collection), and opened almost every top-level folder. Measured warm: about 0.3 s for a 15k-file folder, about 7 s (scan plus tree build) for a 150k-file one, and some 500 rows at load. Reading one folder level takes under a millisecond. The watched intaker repeated the scan every interval. Finding one file in a big folder meant opening folders by hand.

## Decision

1. **The tree lists one folder at a time, on expand, on its own worker thread.** Its intaker from `project.all_files` is never walked: the link runtime hands the tree a collection with the root alone (`LinkRuntime._deliver_listing`), and the tree lists folders with `specto_intake.listing.TreeLister` over that intaker's sources and screener. An archive lists its members when it is expanded. A tree fed explicit files (another tree's picks) is shown whole, as before.
2. **Everything starts collapsed**, except the chain of single-child folders from the root and the ancestors of what is selected.
3. **`project.all_files` keeps its meaning, resolved only by a consumer.** Pinned intakers and anything else off it scan their own sources as before; a canvas holding only a tree does no recursive scan. `LinkRuntime.project_files` (the count on the project node, the prompt bar's folder summary) is set only when a whole-folder intaker off the project source resolves; the prompt bar otherwise walks the project once, cached, when a prompt needs it.
4. **A folder pick still means every file under it**, resolved recursively at pick time on the worker (`TreeLister.files_under`), with the stat the walk saw, so `selected_files` is unchanged for downstream views; only the latest pick's answer goes out. Files and fully listed folders go out at once.
5. **Reveals load their way down.** A restored `params.selected`, the `selection` input and `canvas.open_file` list the ancestor folders they need, then select, open and scroll to the rows.
6. **Counts only where known.** A closed folder shows its file count once every folder below it has been listed; the footer says the file count when it is known, else the rows listed or the files picked; details mode shows a folder's size and newest time only where known. Never a scan for a count.
7. **Auto-update is the tree's own and covers the loaded folders only.** While its intaker says `watch`, the tree lists the folders it has loaded again every watch interval and applies what changed in place; the runtime runs no watcher for that link. A refresh hands the tree its root again, which does the same once.
8. **Type-to-search (#232) is a separate seam, `FileSearch`:** a query for a root, text and limit, cancellable, on the worker, with two implementations: Everything on Windows (its SDK DLL over `ctypes`, talking to the running service; no process per keystroke) and a cancellable walk everywhere else, and on Windows when Everything is not running or the root is not indexed. Hits pass through the tree's screener (the index knows nothing of it), so the query asks for more than the limit. Everything is for search only, never the source of a collection: it does not see inside archives and may miss files its settings exclude, so it cannot back `all_files`.

## Measured (2026-10-31, warm, `tools/bench_tree_open.py`)

| Folder | Files | First rows | Folder listed | Root pick (every file, on the worker) | Before (#231) |
|---|---|---|---|---|---|
| a 15k-file folder | ~15k | 3-13 ms | 8-17 ms | 0.2 s | 0.3 s scan + build |
| a 150k-file folder | ~150k | 3-7 ms | 8-12 ms | 1.7 s | about 7 s scan + build |

A name search under the 15k-file folder through the Everything DLL: about 20 ms from
query to hits (es.exe: 150-180 ms).

Why Everything counts fewer files than a walk (#232): entirely archive members and the
`es` console. On the 150k-file folder about a quarter of the walk's files are on disk
and the rest are members of a handful of archives (two of them with tens of thousands
of entries); Everything holds every on-disk file plus the archives themselves. On the
15k-file folder the difference is a few hundred archive members, and `es` prints names
with non-ASCII characters (°, –) as replacement characters, which made them look
missing. Through the DLL's wide-char API those names come back intact. The index on
the measuring machine excluded nothing (`exclude_folders` and `exclude_files` empty).

## Consequences

- Opening a big folder costs one folder listing; the 150k-file folder shows rows in about 10 ms.
- Views fed from `project.all_files` are unchanged; the tree's collection no longer carries items, so nothing may read the project's files off it (the Python link path already handed scripts `root` instead).
- The project node shows no count until some whole-folder intaker resolves; the prompt bar's summary comes from its own cached walk.
- Watching a folder the tree has not loaded shows nothing until it is opened; arrivals under loaded folders show in place.

## Considered options

- Keep the scan but build the tree lazily from it: rejected; the scan is the cost, and it repeats while watched.
- Count the whole folder with Everything where available: rejected; its counts differ from a walk (archives, excluded files), and a count that is sometimes wrong is worse than none.
- A process per search (`es.exe`): rejected; 150-180 ms per query and a dependency a fresh machine lacks. The DLL talks to the same service in a few milliseconds.
