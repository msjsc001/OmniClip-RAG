# OmniClip RAG / 方寸引 v0.1.7

## Summary

`v0.1.7` is the first release that treats OmniClip RAG as an evidence-first local retrieval layer instead of a page-dumping RAG prototype.

The two goals of this release are:

1. make query output look like focused source notes instead of noisy whole pages,
2. make the desktop workflow predictable enough that users can trust it more than a plain keyword tool.

## Highlights

- Reworked context export to group by note title and emit numbered Markdown snippets with much higher source fidelity.
- Resolved Logseq `((uuid))` refs and `{{embed}}` blocks back into readable text so exported context no longer leaks raw UUIDs.
- Added page-title regex filtering and sensitive-content redaction, including the default `[被RAG过滤/Filtered by RAG]` masking path for high-risk secrets.
- Rebalanced retrieval so lexical and vector candidates are merged before scoring, while single-character searches stay lexical-only to avoid semantic noise.
- Reworked the desktop UI into `Query` and `Config`, with sortable results, text find inside long panels, context jump statistics, layout persistence, and rebuild confirmation.

## Retrieval Contract

This release deliberately tightens the RAG contract:

- retrieval works at the chunk / outline-block level,
- export works as source-faithful evidence,
- final context packs are cleaner than raw search internals,
- relevance is shown as a `0-100` engineering score rather than implied model confidence.

That score is not a probability. It is built from:

- title / path / body lexical hits,
- FTS rank,
- LIKE hits,
- vector similarity,
- penalties for overly long or weak-coverage text.

## Release Shape

- GitHub source push: code, docs, tests
- GitHub release asset: lightweight Windows package
- Included in release asset: `OmniClipRAG.exe`, app files, `InstallRuntime.ps1`, `RUNTIME_SETUP.md`
- Not included in release asset: local `runtime/`, model cache, user data, indexes, exports

## Notes

If you only need the packaged app, download the release asset and run it directly.

If you need local vector retrieval from the packaged build, install the optional runtime separately after extraction. The heavyweight runtime is intentionally kept out of both Git history and the main Windows release package.
