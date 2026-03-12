# OmniClip RAG / 方寸引 v0.2.0

## Summary

`v0.2.0` is the release where the new Qt shell stops feeling transitional and starts feeling like the real product.

The work in this version was concentrated on one goal: close the state-flow gaps, finish the desktop UX rewrite, and keep the public Windows package lean enough to distribute comfortably.

## Highlights

- Rebuilt the packaged desktop flow around the Qt `Query + Configure` shell, removing the legacy packaged UI path and keeping theme/scale/runtime controls together.
- Added a persistent bilingual language selector in the top bar so the visible UI can switch between `简体中文` and `English` without restarting the app.
- Hardened index/watch/query state transitions so cancelled or incomplete rebuilds no longer masquerade as a ready index after restart.
- Smoothed rebuild progress and remaining-time feedback, including more honest vector-tail reporting during large rebuilds.
- Fixed the late-stage large-vault vector-write crash and kept the Windows release on the lean `launcher.exe + _internal + InstallRuntime.ps1 + RUNTIME_SETUP.md` model.

## Release Shape

- GitHub source push: code, docs, tests, release notes
- GitHub release asset: lightweight Windows package zip built from `dist/OmniClipRAG/`
- Included in release asset: `launcher.exe`, `_internal`, `InstallRuntime.ps1`, `RUNTIME_SETUP.md`
- Not included in release asset: local `runtime/`, model cache, user data, indexes, exports

## Notes

If you already keep a local packaged runtime under `dist/OmniClipRAG/runtime/`, rebuilding the app continues to preserve that folder locally. The uploaded release zip stays runtime-free on purpose so GitHub downloads remain small and first-launch setup stays explicit.
