# OmniClip RAG v0.4.11 Release Notes

## Release Status

`v0.4.11` is the Windows Commit-pressure, native PyTorch-crash prevention, and semantic-query recovery release. The packaged Windows GUI has been exercised against a real Runtime and two existing vault indexes before publication.

## Release Focus

This release keeps useful semantic retrieval available when Windows has enough resources for the CUDA embedding model but not enough Commit headroom to safely load the optional Reranker beside it.

Auto mode now preserves GPU/CUDA vector recall and skips only second-pass reranking when Commit headroom is below the measured safe loading floor. The Query Console explains what happened and gives concrete recovery steps instead of suggesting that a healthy Runtime or model must be reinstalled.

## Highlights

- CUDA Markdown vector recall remains active when Auto skips the Reranker for low Commit headroom.
- Auto requires at least 10 GiB of Windows Commit headroom before loading the Reranker beside an active vector model.
- The Query Console tells users to close memory-heavy applications, keep Auto selected, enable automatic Windows paging-file management or increase the paging file, and restart Windows if needed.
- Low-Commit guidance explicitly states that Runtime and model reinstallation are not required.
- Disposable query workers no longer move large CUDA models back to CPU during shutdown.
- Query results are written before the operating system reclaims the disposable worker and its model allocations.
- If a GPU semantic process still exits natively, a fresh CPU semantic process without Reranker is attempted before the final lexical-only fallback.
- The main Qt interface remains isolated from PyTorch, SentenceTransformer, CrossEncoder, and CUDA native-process failures.

## Release Assets

- `OmniClipRAG-v0.4.11-win64.zip`
  - desktop GUI package
- `OmniClipRAG-MCP-v0.4.11-win64.zip`
  - manual MCP package for direct `stdio` setup
- `omniclip-rag-mcp-win-x64-v0.4.11.mcpb`
  - MCPB package for the official Registry and MCPB-aware clients

## Validation Scope

- Focused Query Console ownership regression for the low-Commit remediation text.
- Query-worker cleanup, CPU semantic recovery, and lexical last-resort regressions.
- Reranker low-Commit guard and safe model-release regressions.
- Full automated test suite.
- Packaged GUI and MCP build audits.
- MCPB validation and unpack verification.
- Real packaged multi-vault query using CUDA vector recall and returning 15 results without a native child-process crash.

## Privacy and Compatibility

- Release archives exclude local Runtime installations, model caches, vault contents, indexes, logs, configuration files, credentials, Registry tokens, and temporary query payloads.
- Markdown, PDF, Tika, index, Runtime-layout, and MCP tool contracts are unchanged.
- Auto may skip only the optional Reranker under measured low-Commit conditions; vector semantic recall remains active.
- Explicit CPU/CUDA device selection behavior remains available, while Auto continues to choose the safer path from measured resources.
