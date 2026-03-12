# OmniClip RAG / 方寸引 v0.2.1

## Summary

`v0.2.1` is the stabilization pass that follows the Qt rewrite release.

The focus of this version was simple: keep the public Windows package lean, but remove the last confusing edges around runtime readiness, rebuild progress honesty, and large-vault memory-pressure recovery.

## Highlights

- Clarified the lightweight packaged workflow so model bootstrap, CUDA/runtime guidance, and vector-runtime readiness no longer blur together.
- Added richer device/runtime status and rolling file logging controls in the Configure flow, making packaged troubleshooting much more transparent.
- Hardened vector rebuild recovery for large vaults under RAM/VRAM pressure with proactive shrinking, backpressure yielding, and smaller writer retries.
- Unified rebuild progress around one overall percentage while still exposing vector-stage detail such as encoded, written, flushing, and recovering states.
- Fixed the optional reranker contract so disabling it in settings really disables reranker execution.

## Release Shape

- GitHub source push: code, docs, tests, release notes
- GitHub release asset: lightweight Windows package zip built from `dist/OmniClipRAG/`
- Included in release asset: `launcher.exe`, `_internal`, `InstallRuntime.ps1`, `RUNTIME_SETUP.md`
- Not included in release asset: local `runtime/`, model cache, user data, indexes, exports, logs

## Notes

Local rebuilds still preserve an existing `dist/OmniClipRAG/runtime/` folder on your own machine. The uploaded release zip remains runtime-free on purpose so GitHub downloads stay small and users opt into heavyweight local runtimes explicitly.
