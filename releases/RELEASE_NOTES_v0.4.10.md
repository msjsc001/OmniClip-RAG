# OmniClip RAG v0.4.10 Release Notes

## Release Status

`v0.4.10` is the Runtime-readiness, query-isolation, CUDA diagnostics, and desktop workspace-layout release. The packaged Windows build has been exercised against a real Runtime and multi-vault configuration before publication.

## Release Focus

This release prevents startup Runtime detection and native semantic-model failures from taking control of the interactive desktop process.

The query interface stays blocked while the automatic Runtime probe is active. Safe startup still skips semantic prewarming, but it no longer skips the isolated status-only probe that supplies the Start and Runtime pages with an accurate state.

Queries now run in a disposable child process. A native Windows access violation in PyTorch, SentenceTransformer, CrossEncoder, CUDA, or another compiled dependency can end that query process without terminating the Qt main window. If the semantic process exits without a result, OmniClip makes one clean lexical-only attempt in a fresh process and displays an explicit degradation warning.

## Highlights

- Query buttons and the execution guard remain disabled while startup Runtime detection is active.
- Safe startup performs the isolated Runtime status probe instead of leaving a stale repair warning.
- The exact Runtime probe payload is applied to both the Start and Runtime pages.
- Single-vault and multi-vault queries execute outside the GUI process.
- Query progress and structured results cross a temporary JSON protocol whose files are removed after each query.
- Native semantic-process crashes cannot directly terminate the desktop window.
- A fresh lexical-only process provides one automatic recovery attempt.
- Closing the desktop terminates an active query process so it is not orphaned.
- Multi-vault queries continue to reuse the same embedding and reranker resources within one query.
- Auto now leaves ordinary Windows memory pressure to the operating system and intervenes only near the commit/physical-memory limit; the query still retains isolated-process and CUDA-OOM recovery protection.
- The Query console and Activity log report the measured memory values, the exact CUDA/Reranker readiness conditions, and the original Reranker exception instead of presenting an unexplained failure.
- Healthy memory snapshots no longer generate a false CPU-fallback warning, and CUDA execution is identified from the model/inference device rather than the CPU location of the returned NumPy array.
- Query Console, Results & Details, Config, and Activity Log now occupy independent top-level tabs; existing diagnostic jumps open the new Activity Log location.
- The header can be hidden completely while its Expand/Collapse action remains at the far right of the tab row, and a compact state button beside Query shows whether a query is ready, blocked, running, or complete.
- Official MCP Registry discovery now follows the current `io.github.EllisMorrow/omniclip-rag-mcp` namespace; the MCPB keeps its legacy internal identity so existing installations remain upgrade-compatible.

## Release Assets

- `OmniClipRAG-v0.4.10-win64.zip`
  - desktop GUI package
- `OmniClipRAG-MCP-v0.4.10-win64.zip`
  - manual MCP package for direct `stdio` setup
- `omniclip-rag-mcp-win-x64-v0.4.10.mcpb`
  - MCPB package

## Validation Scope

- Startup Runtime gating and safe-mode status decisions.
- Start-page Runtime state synchronization.
- Query result serialization, progress transport, and native-crash recovery coordination.
- A real source-build child-process round trip against a synthetic Markdown vault.
- Full automated test suite, packaged build audit, MCPB validation/unpack, and packaged desktop smoke testing before publication.

## Privacy and Compatibility

- The release archives exclude local Runtime installations, vault contents, indexes, logs, caches, configuration files, credentials, and Registry tokens.
- The external Runtime layout, Markdown/PDF/Tika source formats, indexes, and MCP tool contracts are unchanged.
- Query recovery changes ranking behavior only when the semantic child process has already failed; normal successful semantic queries retain the configured hybrid pipeline.
