# OmniClip RAG / 方寸引 v0.1.6

## Summary

`v0.1.6` focuses on the packaged-runtime path for the lean Windows release.

This version does not try to bundle heavy AI runtimes into the Git repository. Instead, it makes the external `runtime/` flow much more reliable and keeps the source release clean.

## Highlights

- Fixed `InstallRuntime.ps1` so CUDA installs no longer get overwritten by a later CPU-only `torch` resolution step.
- Added runtime bootstrap metadata plus startup search-path recovery for packaged builds that rely on an external `runtime/` directory.
- Preserved `dist/OmniClipRAG/runtime/` across local EXE rebuilds so already-installed runtime files are not deleted.
- Kept Git and source pushes clean by continuing to exclude `runtime/`, `dist/`, EXEs, and other large generated files from version control.

## Release Shape

- GitHub source push: code and documentation only
- GitHub release asset: lightweight Windows package
- Optional runtime: installed separately through `InstallRuntime.ps1`
- Model files: still user-managed and stored outside the repository

## Notes

If you need local vector indexing or local semantic retrieval from the packaged app, install the optional runtime after extracting the release package. The heavyweight runtime is intentionally not tracked in Git.
