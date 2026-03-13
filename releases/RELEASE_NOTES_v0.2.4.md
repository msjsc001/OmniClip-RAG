# OmniClip RAG / 方寸引 v0.2.4

## Summary

`v0.2.4` is a narrow UI-readability release built on top of `v0.2.3`.

The goal of this release is simple: once a query has returned, users should be able to see which result they are reading immediately, and the top query row should stop competing with the results area through redundant copy buttons.

## Highlights

- Added a dedicated leftmost sequence column in the Qt results table.
- Removed `Search and copy` and `Copy current context` from the top query toolbar.
- Rebalanced result-table widths so the new row index does not wreck the main reading columns.
- Kept the retrieval backend untouched; this is a presentation and workflow cleanup release.

## Release Shape

- GitHub source push: code, docs, tests, release notes
- GitHub release asset: lightweight Windows package zip built from `dist/OmniClipRAG-v0.2.4/`
- Included in release asset: `OmniClipRAG.exe`, `_internal`, `InstallRuntime.ps1`, `RUNTIME_SETUP.md`
- Not included in release asset: local `runtime/`, model cache, user data, indexes, exports, logs

## Notes

This release intentionally does not change query semantics or retrieval ranking. It only tightens the visual review flow after search results are already back, so the visible behavior should be easier to read without changing what the backend retrieves.
