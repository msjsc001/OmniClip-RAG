# OmniClip RAG v0.4.12 Release Notes

## Release Status

`v0.4.12` is the adaptive Reranker-resource, visible query-stage, and query-process cleanup release.

## Release Focus

This release removes the universal 10 GiB Windows Commit floor that could skip a healthy Reranker even when the machine had enough RAM and VRAM for the actual workload.

Auto now estimates the active Reranker's requirements from the measured local model weights and query batch settings, then compares that estimate with live Windows Commit headroom, available physical memory, and free CUDA memory. The Query Console exposes those measured and required values whenever Auto must skip only the optional second-pass ordering.

## Highlights

- Auto uses a model-aware resource estimate instead of a universal 10 GiB Commit threshold.
- Explicit CUDA remains an intentional user override while desktop queries stay isolated from the main interface.
- The status button beside Search shows the live query stage and percentage.
- Visible stages include preparation, lexical recall, semantic recall, fusion, reranking, result finalization, context packing, copying, and fallback recovery.
- Failed queries keep a clear failed state instead of showing an earlier completed query.
- Score-threshold and result-limit fields remain immediately beside their labels on wide windows.
- Completed, cancelled, and failed query children are explicitly waited on and reaped.
- Global live-watch stop now targets every actual running vault, even if the selected-vault set changed after startup.
- Each vault exposes a non-repeatable stopping state while its worker exits.
- Initial vault scans, incremental rendering, and vector writes honor cooperative stop requests and retain repair state when interrupted.
- Application shutdown waits for live-watch workers instead of immediately discarding their bookkeeping.
- PDF/Tika directory actions keep state reloads and indexed-source summaries off the Qt UI thread.
- Repeated PDF/Tika refresh requests are coalesced, and stale background results cannot overwrite newer directory settings.

## Release Assets

- `OmniClipRAG-v0.4.12-WIN-EXE.zip`
  - ordinary Windows desktop GUI package; the explicit `WIN-EXE` label makes the end-user download unambiguous
- `OmniClipRAG-MCP-v0.4.12-win64.zip`
  - manual MCP package for direct `stdio` setup
- `omniclip-rag-mcp-win-x64-v0.4.12.mcpb`
  - MCPB package for the official Registry and MCPB-aware clients

## Validation Scope

- Focused Query Console regressions for live stage text, failed state, and numeric-control ownership.
- Adaptive Reranker resource-budget and explicit CUDA override regressions.
- Query-worker completion and cancellation reaping regressions.
- Multi-vault live-watch stop, duplicate-stop suppression, cooperative vector cancellation, and worker-exit regressions.
- PDF/Tika directory-button responsiveness, background-thread ownership, stale-result rejection, and pending state-reload regressions.
- Full automated test suite.
- Packaged GUI, MCP, and MCPB build audits.
- Packaged multi-vault query verification with CUDA vector recall and CUDA Reranker execution.

## Privacy and Compatibility

- Release archives exclude local Runtime installations, model caches, vault contents, indexes, logs, configuration files, credentials, Registry tokens, and temporary query payloads.
- Markdown, PDF, Tika, index, Runtime-layout, and MCP tool contracts are unchanged.
- Auto can still skip only the optional Reranker when the model-aware estimate says current resources are insufficient.
- Explicit CPU/CUDA device selection remains available.
