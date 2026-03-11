# OmniClip RAG / 方寸引 v0.1.11

## Summary

`v0.1.11` is the Windows release focused on desktop interaction polish, query-state clarity, and UI readability.

This release concentrates on four things:

1. making result review easier with note-level page sorting,
2. making query execution state obvious at a glance,
3. reducing visible GUI lag caused by layout rebuilds and drag-time redraw pressure,
4. shipping a lean EXE release asset without `runtime/` while still preserving local packaged runtime folders across rebuilds.

## Highlights

- Added a `Page Sort` toggle in the results table so current hits can be regrouped by note and ordered by average fragment score.
- Added a dedicated query-status banner with clear `idle / blocked / running / done` states and backend-driven stage progress.
- Added a new `UI` settings page with persisted text scaling and `Light / Dark / Follow system` themes.
- Reduced GUI interaction lag by removing full-window rebuilds from routine toggles and coalescing high-frequency layout callbacks during pane dragging.
- Kept the public release artifact runtime-free while preserving any existing local `dist/OmniClipRAG/runtime/` folder during rebuilds.

## Release Shape

- GitHub source push: code, docs, tests, release notes
- GitHub release asset: packaged Windows EXE zip
- Not included in the release asset: `runtime/`, model cache, user data, indexes, exports

## Notes

This release is aimed at making the desktop app feel more trustworthy during normal daily use: less ambiguous query feedback, less obvious redraw stutter, clearer page-level result inspection, and better readability controls for long sessions.
