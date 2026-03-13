# OmniClip RAG / 方寸引 v0.2.2

## Summary

`v0.2.2` is the durability and packaging-shape follow-up to `v0.2.1`.

The goal of this release is direct: make very large full rebuilds more resumable after interruption, and make packaged builds safer to keep side by side on a real machine without wiping local runtimes.

## Highlights

- Reworked rebuild-state persistence so checkpoints stay compact even when the vault grows very large.
- Added more durable rebuild resume behavior for rendering and vector stages, including suffix rewind before vector continuation.
- Added watchdog diagnostics plus safe-startup recovery after RAM/VRAM incidents and dirty exits.
- Changed packaged builds to land in versioned folders such as `dist/OmniClipRAG-v0.2.2/`, leaving older local build folders intact.
- Renamed the packaged executable to `OmniClipRAG.exe`, aligning the EXE, Windows process name, and runtime instructions with the product name.

## Release Shape

- GitHub source push: code, docs, tests, release notes
- GitHub release asset: lightweight Windows package zip built from `dist/OmniClipRAG-v0.2.2/`
- Included in release asset: `OmniClipRAG.exe`, `_internal`, `InstallRuntime.ps1`, `RUNTIME_SETUP.md`
- Not included in release asset: local `runtime/`, model cache, user data, indexes, exports, logs

## Notes

Local rebuilds now preserve any `runtime/` folder inside the current versioned build directory, while older versioned build folders remain untouched. The uploaded release zip stays runtime-free on purpose so GitHub downloads remain small and heavyweight local runtimes are still an explicit user-side install.
