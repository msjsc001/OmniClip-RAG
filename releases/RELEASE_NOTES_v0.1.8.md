# OmniClip RAG / 方寸引 v0.1.8

## Summary

`v0.1.8` is a reliability release focused on hardening live watch for real local-vault edge cases.

This version is about one promise: editing notes should not silently damage a still-valid index just because a file is temporarily locked, half-written, or the vault briefly goes offline.

## Highlights

- Reworked incremental reindex into a parse-first flow, so a changed file only replaces old index rows after successful parsing.
- Added a unified watch pipeline for both polling and watchdog: snapshot diffing, file-stability delay, delete-confirmation delay, and manifest reconciliation.
- Added offline guard behavior for missing/unmounted vault roots, so encrypted-drive dropouts do not get treated as mass deletion.
- Added persisted watch recovery state plus repair replay for dirty rendered chunks and dirty vector work.
- Added desktop activity-log visibility for watch offline/recovered/repaired/retry events.

## Reliability Contract

This release deliberately tightens the hot-watch contract:

- SQLite remains the source of truth.
- Vector writes may be temporarily dirty, but they are tracked and repaired.
- Temporary read failures keep the previous indexed content alive.
- Missing files must survive a delete-confirmation window before they are removed.
- Vault-offline states freeze destructive updates instead of guessing.

## Release Shape

- GitHub source push: code, docs, tests
- GitHub release asset: lightweight Windows package
- Included in release asset: `OmniClipRAG.exe`, app files, `InstallRuntime.ps1`, `RUNTIME_SETUP.md`
- Not included in release asset: local `runtime/`, model cache, user data, indexes, exports

## Notes

If you already have a local packaged runtime installed under `dist/OmniClipRAG/runtime/`, rebuilding the EXE still preserves that local runtime. The public release package remains lean and keeps `runtime/` out of both Git history and the uploaded release asset.
