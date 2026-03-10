# OmniClip RAG / 方寸引 v0.1.10

## Summary

`v0.1.10` is the packaged Windows release that turns the recent retrieval-optimization and rebuild-stability work into a testable EXE.

This release focuses on three things:

1. making retrieval controls easier to understand and use,
2. reducing repeated branches in exported full context,
3. shipping the stabilized large-vault rebuild improvements as a Windows package.

## Highlights

- Added a dedicated `Retrieval Boost` settings page for reranker controls, AI-collaboration export, CPU / GPU batch sizing, and reranker readiness.
- Fixed reranker bootstrap so users can download or manually stage the reranker model before enabling reranking, with duplicate-download protection and exact cache-folder guidance.
- Fixed final minimum-relevance filtering so the visible result list now respects the final displayed score after reranking.
- Improved full-context export by compactly merging same-parent sibling fragments when they clearly belong to one local structure.
- Packaged the current large-vault rebuild improvements: hardware-peak profiles, adaptive encode/write batch tuning, phase-aware ETA, stable single-writer vector writes, and deeper late-stage write observability.

## Release Shape

- GitHub source push: code, docs, tests, release notes
- GitHub release asset: packaged Windows EXE zip
- Not included in the release asset: `runtime/`, model cache, user data, indexes, exports

## Notes

This release is intended to be the first packaged checkpoint after the recent retrieval-optimization and rebuild-performance work. It is meant to be tested against larger Markdown / Logseq vaults, especially cases where old builds slowed dramatically in the late vector-write stage.
