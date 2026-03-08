# OmniClip RAG v0.1.0

**OmniClip RAG** is now public as the first desktop-first release candidate of a local-first Markdown / Logseq retrieval layer.

This release focuses on one core idea:

> keep your notes local, keep your retrieval layer separate, and connect any AI to your knowledge only through the context you explicitly choose to expose.

## Highlights

- Desktop GUI for configuration, preflight, model bootstrap, indexing, querying, watch mode, and selective cleanup
- Hybrid retrieval stack with `SQLite + FTS5 + LanceDB`
- Logseq-aware parsing with support for page properties, block properties, `id:: UUID`, block refs, and block embeds
- Local `BAAI/bge-m3` embedding support
- Preflight disk estimation before large local operations
- Hot-reload and incremental reindexing
- Context-pack export designed for copy/paste into any AI workflow

## Why This Release Matters

Many note-to-AI tools are tightly coupled to one product or one hosted workflow.

OmniClip RAG takes a different position:

- your vault remains local,
- your retrieval layer remains independent,
- any AI can consume the final context pack,
- and full-vault exposure is not the default interaction model.

## Technical Shape

- Parser: standard Markdown + Logseq Markdown
- Metadata authority: SQLite
- Lexical retrieval: FTS5 + LIKE
- Vector retrieval: LanceDB
- Embedding model: `BAAI/bge-m3`
- Primary user surface: desktop GUI
- Secondary user surface: CLI

## Validation

This release has already been verified with:

- automated unit tests,
- sample vault indexing,
- GUI startup validation,
- EXE packaging validation,
- CLI query validation.

## Current Scope

`v0.1.0` is intentionally focused on the core product loop:

1. parse the vault,
2. build the index,
3. search locally,
4. inspect the results,
5. export a context pack,
6. hand it to any AI.

It does **not** yet try to ship everything at once.

## Known Next Steps

- reranker support
- tray mode and global hotkeys
- richer settings panels
- a leaner ONNX-first path for production packaging

## Documentation

- English README: [README.md](../README.md)
- Chinese README: [README.zh-CN.md](../README.zh-CN.md)
- Architecture: [ARCHITECTURE.md](../ARCHITECTURE.md)
- Changelog: [CHANGELOG.md](../CHANGELOG.md)

## Short Release Summary

OmniClip RAG v0.1.0 is the first public desktop release of a local-first, high-decoupling RAG bridge for Markdown and Logseq vaults.
