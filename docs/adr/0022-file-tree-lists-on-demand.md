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
6. **Counts only where known.** A closed folder shows its file count once every folder below it has been listed; the footer says the file count when it is known, else the rows listed or the files picked; details mode shows a folder's own modified time (from the directory entry its parent's listing read, #38) and, once it is listed, how many items it shows; its total size and newest time only where known. Never a scan for a count.
7. **Auto-update is the tree's own and covers the loaded folders only.** While its intaker says `watch`, the tree lists the folders it has loaded again every watch interval and applies what changed in place; the runtime runs no watcher for that link. A refresh hands the tree its root again, which does the same once.
8. **Type-to-search (#232) is a separate seam, `FileSearch`:** a query for a root, text and limit, cancellable, on the worker, with two implementations: Everything on Windows (its SDK DLL over `ctypes`, talking to the running service; no process per keystroke) and a cancellable walk everywhere else, and on Windows when Everything is not running or the root is not indexed. Hits pass through the tree's screener (the index knows nothing of it), so the query asks for more than the limit. Everything is for search only, never the source of a collection: it does not see inside archives and may miss files its settings exclude, so it cannot back `all_files`.
9. **A search word matches anywhere in the path below the root (#36), never above it.** Both backends check every word against the relative tree path, case-insensitively, a slash in a word matching across the separator; names holding every word rank above files reached only through their folders, then the order of #232. Everything matches `path:` terms against the absolute path, so a word that occurs in the root's own path goes to it as a regex anchored at the root instead (a pass over the files under the root, about 1.5 µs each); every candidate is checked against the relative path locally; when the first page leaves the limit unfilled the candidates are read through up to a bound (`INDEX_BOUND`, 50k; two pages at most), past which the walk answers the query, so `more` is only said when it is true.

## Amendment 2026-11-05: the hits show inside the tree (SPEC-38)

The hits no longer replace the tree with a flat list. While the search box has text the tree's own list shows the **search tree**: each hit under the folders above it, every one of those folders open, every other row hidden.

- **Built from the hits alone.** The search tree is made from the hit paths the backend returned, never from listing the project and never by loading the hits' folders into the tree. A folder that is only on the way to a hit is *narrowed*: it holds the hits under it and nothing else, and its details line counts them (`3 hits`) rather than claiming an item count or a total it does not know. The result cap and the "more not shown" note apply as before.
- **Folders can be hits, by their own name.** `FileSearch.search` takes a `HitFilter` (the lister's `hit_filter()`: files `accepts`, folders `accepts_dir`, under a source and not `.specto/`); with a folder predicate, a folder whose own name holds every word is a hit, ranked as a file is. Files keep matching by their whole path (decision 9), but a folder does not: by path, every subfolder of a matching folder would be a hit too and fill the cap. Everything then runs without its `file:` filter and reads each result's folder flag; the scan checks the folders it walks.
- **A folder hit is a real folder.** With hits under it, it opens and is listed in full on the tree's worker, so it shows what it holds beside its hits; with none, it shows closed and is listed when opened. Either way it is listed as any folder is on expand: one folder, on demand.
- **The tree's state is left alone.** The tree's own rows, open folders and scroll position do not change during a search, so clearing the text brings them back. The picks are one set for both: a pick made on a hit row stays picked after the search (it has gone out on `selected_files` already); the row's reveal button is the way to show it in the tree, which lists the folders down to it (decision 5). Picks set from outside during a search (a received selection, `params.selected` from the agent or the open gesture) take effect at once, paths neither tree holds included, so the picks equal `params.selected`; the hidden tree is neither listed nor opened for them until the search clears, when they are shown as decision 5 says, unless the user picked since.
- **Rows are rows.** The view's gestures name rows of whichever tree the list shows, so click, Ctrl, Shift, the chevron, double-click, Ctrl+double-click, the context menu and drag behave on hit rows as on the tree's, and Down from the search box moves the keys to the first hit row (Up from the top row goes back). A picked folder in the search tree stands for every file under it on disk (decision 4), not just its hits.

Considered: loading the hits' folders into the tree and filtering its rows (rejected: each hit's folders would be listed, against decision 1, and the filter would leave half-listed folders behind); a second list beside the tree (rejected: two delegates to keep aligned, and a second set of gesture entry points, as the flat list had).

## Measured (2026-10-31, warm, `tools/bench_tree_open.py`)

| Folder | Files | First rows | Folder listed | Root pick (every file, on the worker) | Before (#231) |
|---|---|---|---|---|---|
| a 15k-file folder | ~15k | 3-13 ms | 8-17 ms | 0.2 s | 0.3 s scan + build |
| a 150k-file folder | ~150k | 3-7 ms | 8-12 ms | 1.7 s | about 7 s scan + build |

A name search under the 15k-file folder through the Everything DLL: about 20 ms from
query to hits (es.exe: 150-180 ms).

Path search (#36, 2026-11-01, the same 15k-file folder, query to hits, median of 7):
18-21 ms for a word in no folder above the root (`readme`, `notes readme`, `attachments/img`);
24-35 ms for a word that also occurs in the root's own path, which goes as the anchored
regex (a single letter, the folder's own name); the slowest of 70 runs 48 ms. The walk of
the same folder, warm: 85-105 ms.

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
