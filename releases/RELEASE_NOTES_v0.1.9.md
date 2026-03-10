# OmniClip RAG / 方寸引 v0.1.9

## Summary

`v0.1.9` is a source-first release focused on two things:

1. reducing manual fragment triage during retrieval,
2. making large-vault rebuild speed and ETA behave more like the real machine state.

This release intentionally does **not** ship a new EXE asset. It publishes the code, tests, docs, and release notes only.

## Highlights

- Added backend-only retrieval shaping modules so ranking, same-page novelty selection, reranking, and AI-collaboration export no longer depend on GUI-side logic.
- Added optional `BAAI/bge-reranker-v2-m3` support with manual bootstrap, batching, truncation, CUDA OOM recovery, CPU fallback, and safe-skip behavior.
- Added adaptive rebuild throughput control with `30% / 50% / 90%` hardware-peak profiles and independent encode/write batch tuning.
- Reworked rebuild ETA so it blends static history, current-stage recent throughput, and previous vector tail-speed history instead of trusting one blunt average.
- Moved long-form planning docs into `plans/` to keep strategy documents organized and out of the project root.

## Release Shape

- GitHub source push: code, docs, tests, release notes
- GitHub release assets: none added manually in this release
- Not included: `OmniClipRAG.exe`, `runtime/`, model cache, user data, indexes, exports

## Notes

This release is meant to capture the latest retrieval and rebuild architecture cleanly without disturbing users who are still running a long local EXE build. When you are ready, build the new EXE from source or wait for the next packaged Windows release.
