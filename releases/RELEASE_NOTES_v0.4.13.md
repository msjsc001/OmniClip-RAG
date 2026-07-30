# OmniClip RAG v0.4.13 Release Notes

## Release Status

`v0.4.13` is the event-driven live-watch and disposable incremental-indexing release.

## Release Focus

This release removes recurring whole-vault scans from normal Markdown `watchdog` operation. After one startup reconciliation, live watch waits for real file events, merges repeated saves during a configurable quiet period, and processes only the affected Markdown files.

Incremental parsing, embedding, vector writes, and CUDA work now run in a disposable child process. When the batch completes or live watch is stopped, the process exits so Windows can reclaim its Commit charge, physical memory, and GPU memory.

## Highlights

- Markdown `watchdog` mode is event-driven after startup reconciliation.
- The live-watch quiet period is configurable and defaults to 10 seconds.
- Repeated saves are coalesced into one incremental update after editing settles.
- Heavy incremental indexing runs outside the GUI process and releases resources after every batch.
- Multiple watched vaults serialize semantic update batches to avoid duplicate model loads.
- Stop requests terminate and reap active incremental workers.
- `.stversions`, `logseq/bak`, `logseq/.recycle`, and `logseq/.tine-trash` are excluded from Markdown indexing and watch events.
- Query subprocess resource cleanup and existing Markdown/PDF/Tika/MCP behavior remain unchanged.

## Release Assets

- `OmniClipRAG-v0.4.13-WIN-EXE.zip`
  - ordinary Windows desktop GUI package; use this package for desktop testing
- `OmniClipRAG-MCP-v0.4.13-win64.zip`
  - manual MCP package for direct `stdio` setup
- `omniclip-rag-mcp-win-x64-v0.4.13.mcpb`
  - MCPB package for the official Registry and MCPB-aware clients

## Validation Scope

- Event coalescing and configurable quiet-period regressions.
- Event-only idle-watch behavior after startup reconciliation.
- Generated Logseq history/recovery exclusion regressions.
- Disposable watch-worker lifecycle, cancellation, and process-reaping regressions.
- Multi-vault serialization and resource-batch mapping regressions.
- Synthetic real-watch smoke test using filesystem events and an actual disposable worker.
- Full automated test suite.
- Packaged GUI and MCP build audits.

## Privacy and Compatibility

- Release archives exclude local Runtime installations, model caches, vault contents, indexes, logs, configuration files, credentials, Registry tokens, and temporary worker payloads.
- Existing configuration files remain compatible; the new quiet-period value defaults to 10 seconds when absent.
- Markdown, PDF, Tika, query, Runtime-layout, and MCP contracts are unchanged.
- Polling remains available only as a compatibility fallback when filesystem event monitoring cannot be used.
