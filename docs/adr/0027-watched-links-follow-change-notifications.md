---
status: accepted
date: 2026-11-06
---

# Watched links follow change notifications, and poll only where there are none

Task SPEC-8. Amends ADR 0016's watch mode (story 57): how a `LinkWatch` learns that a watched link's sources changed.

## Context

A watched intaker listed all its sources every second (`watch_interval_s` 1.0, one `scandir` pass in `watch_intaker`) and compared the listing with the last one. On a folder of 100,000 files that pass takes about a third of a second of Python, so the watcher held about a fifth of a core (and the GIL of the GUI process) for as long as the folder was watched, with nothing arriving. A resolve running beside it slowed down too: an arrival took about 7 s to land as a collection, where the resolve alone takes about 2 s. On a network drive each pass is a full remote directory walk every second.

## Decision

1. **A watcher kind may register a `WatchListing` beside its watcher** (`register_watcher(kind, fn, listing=factory)`). It names the folders to observe (`roots`), and updates the watcher's table in place for the paths a change notice names (`apply`), giving the same table a full listing would. The intaker's is `specto_intake.watch_listing.IntakerListing`: a path that is a file now is stat'ed and kept by the walk's rules (pattern, depth, symlinked folders only when the source follows links, `.specto/` never); a folder now is walked; a path gone is dropped with everything under it, through a folder index so that costs the folder's own entries. A notice for a source root itself asks for a full listing.
2. **Notifications come from watchdog** (Apache-2.0; ReadDirectoryChangesW on Windows, inotify on Linux, FSEvents on macOS), wrapped in `specto_core.fswatch.Notifier`. A folder's own "modified" notice is dropped; both ends of a move are applied. Only those three backends count: watchdog's polling and kqueue fallbacks do not (kqueue opens every file).
3. **The watcher lists everything once, then applies notices.** It starts notifications before its baseline listing, so nothing between the two is lost, and gathers notices until they have been quiet for 50 ms (at most 0.5 s) before applying them. The runtime's `link.arrivals`, its debounce and the resolve are unchanged, so the "+N new" pill, the status line and undo behave as before.
4. **A full listing still runs, as a safety net**: every 30 s (10 s on a network drive), and never closer than 50 listing durations, so a slow listing stays a small share of a core. Also after a burst (1,000 paths in one batch: a listing is cheaper than a thousand stats, and an overflow inside a burst loses notices) and again once the burst has been quiet for 2 s, and whenever Windows reports lost notices. watchdog drops a ReadDirectoryChangesW overflow silently (the call returns no bytes); Specto's observer subclasses its emitter to report it.
5. **Where notifications are not to be trusted, the watcher polls as before and says so.** That is no native backend, a source that is not there yet (tried again after each listing), notifications that would not start (inotify's watch limit) or that stop (a share refusing them, the folder deleted), and network file systems on Linux and macOS (NFS, SMB, sshfs and the like), where the kernel sees only this machine's changes. A Windows network drive keeps notifications, because the SMB server sends them for every client's changes, plus the shorter safety-net interval. The watcher's `mode` (`notify` or `poll`) and `reason` show in `links.watch_status`. The event `link.watching` fires when a watcher settles on its mode and whenever the mode changes. The status bar says "auto-update on (polling: ...)" for a view fed by a polling watcher. `SPECTO_WATCH=poll` forces polling everywhere.
6. **The resolve is not incremental.** An arrival still re-runs the whole intaker on a worker (ADR 0016 note), about 2 s for 100,000 files. Making grouping incremental is a separate change.

## Measured (`tools/bench_watch.py`, a 100,000-file folder in 100 subfolders, Windows, NTFS, warm)

| | Idle CPU (share of one core) | Full listings in 20 s idle | Arrival noticed | Arrival's collection landed |
|---|---|---|---|---|
| Before (poll every 1 s) | 21.5 % | 15 | 0.81 s | 7.0 s |
| After (notifications) | 0.2 % | 0 | 0.63 s | 2.6 s |
| After, over SMB (`\\localhost\C$`) | 0.2 % | 0 | 0.62 s | 2.8 s |
| `SPECTO_WATCH=poll`, over SMB | 23.7 % | 13 | 1.28 s | 3.6 s |

"Arrival noticed" is `link.arrivals`, which turns the view's status to "1 arrived, resolving". The 0.5 s of it is the runtime's debounce. Over 65 s idle with notifications the process used 0.3 % of a core, safety-net listings included.

## Consequences

- A new runtime dependency, watchdog (pinned below 7: 7.0 shipped days before this change).
- Specto's Windows observer depends on two watchdog internals (`winapi.read_directory_changes`, `winapi._parse_event_buffer`). If they move, it falls back to watchdog's own observer, without the lost-notice report; the 30 s listing still catches what an overflow loses.
- Dropbox and similar sync clients write into an ordinary local folder (a temporary file, then a rename into place). The rename's notice names the file, so it arrives as one file.
- The file tree's own auto-update (ADR 0022 decision 7) still relists its loaded folders every interval. That is cheap, since it covers only open folders, and is unchanged.
