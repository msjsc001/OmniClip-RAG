# OmniClip RAG v0.1.2

`v0.1.2` packages the runtime, timing, and naming refinements after `v0.1.1`.

This update is about making the desktop app more truthful about acceleration, more readable during long rebuilds, and more consistent in how the product is presented publicly.

## Highlights

- Chinese product name updated to **方寸引** across the desktop title and Chinese-facing docs
- `auto` device mode now acts as the real default and resolves to CUDA when the active PyTorch runtime genuinely supports your NVIDIA GPU
- Rebuild ETA and precheck timing now learn from recent local build history instead of only static estimates
- Full rebuild control is tighter with explicit cancel handling and interruptible vector batches that pause faster
- Query review is safer with relevance filtering, per-hit include toggles, clearer excerpts, and Unicode-safe clipboard export on Chinese Windows
- Public docs now match the current desktop workflow, data layout, runtime behavior, and lean-release packaging strategy

## What Changed Since v0.1.1

### Product naming

- Renamed the Chinese product name from `无界 RAG` to **方寸引**.
- Updated the desktop window title, Chinese UI wording, and Chinese documentation to match.

### Runtime behavior

- Switched the default device policy to `auto`.
- Confirmed CUDA-capable PyTorch runtime support on compatible NVIDIA systems.
- Kept acceleration reporting honest: hardware detection and runtime readiness are shown separately.

### Lean Windows release packaging

- The official Windows app package no longer tries to bundle very large optional AI runtimes into the main release asset.
- Model files remain user-managed and separate from the app package.
- Heavy runtime components are now documented and installed separately through `RUNTIME_SETUP.md` and `InstallRuntime.ps1`.

### Timing and rebuild control

- Added workspace-level build-history timing for better future ETA baselines.
- Tightened full-rebuild progress reporting so pause / resume / cancel operate on the real vector batching loop.
- Improved paused-state elapsed time and ETA handling so the task panel reflects actual paused time.

### Retrieval review and clipboard flow

- Continued the query-review work with visible score filtering and per-hit inclusion control.
- Fixed clipboard export for formatted context packs on Chinese Windows environments.

## Validation

This update has been validated with:

- automated unit tests,
- Python compile checks,
- CUDA runtime detection checks,
- GUI-side naming and localization checks,
- sample indexing and query regression tests.

## Documentation

- English README: [README.md](../README.md)
- Chinese README: [README.zh-CN.md](../README.zh-CN.md)
- Architecture: [ARCHITECTURE.md](../ARCHITECTURE.md)
- Changelog: [CHANGELOG.md](../CHANGELOG.md)
- Runtime Setup: [RUNTIME_SETUP.md](../RUNTIME_SETUP.md)

## Short Release Summary

OmniClip RAG v0.1.2 turns the recent desktop hardening work into a more coherent release: a clearer Chinese identity, more honest GPU behavior, more credible rebuild timing, and safer final context review before handing anything to external AI tools.
