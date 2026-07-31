<div align="center">

<img src="docs/assets/social-preview.png" alt="OmniClip RAG — Private knowledge. Local retrieval." width="900" />

# OmniClip RAG

**A local-first Windows desktop app and read-only MCP server for searching private Markdown, PDF, and Tika-backed knowledge bases.**

[![Latest Release](https://img.shields.io/github/v/release/EllisMorrow/OmniClip-RAG?style=flat-square&label=release)](https://github.com/EllisMorrow/OmniClip-RAG/releases/latest)
[![Windows](https://img.shields.io/badge/platform-Windows-15584f?style=flat-square)](https://github.com/EllisMorrow/OmniClip-RAG/releases/latest)
[![Local-first](https://img.shields.io/badge/local--first-yes-1d7467?style=flat-square)](#privacy-boundary)
[![Downloads](https://img.shields.io/github/downloads/EllisMorrow/OmniClip-RAG/total?label=downloads&color=brightgreen&style=flat-square)](https://github.com/EllisMorrow/OmniClip-RAG/releases)
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-listed-1f6feb?style=flat-square)](https://registry.modelcontextprotocol.io/v0/servers?search=io.github.EllisMorrow/omniclip-rag-mcp)
[![License](https://img.shields.io/badge/license-MIT-2f7d32?style=flat-square)](LICENSE)

[Download Windows app](https://github.com/EllisMorrow/OmniClip-RAG/releases/latest) ·
[中文说明](README.zh-CN.md) ·
[Website](https://ellismorrow.github.io/OmniClip-RAG/) ·
[User Wiki](https://github.com/EllisMorrow/OmniClip-RAG/wiki) ·
[MCP Setup](MCP_SETUP.md)

</div>

## What OmniClip RAG does

OmniClip RAG builds searchable indexes from knowledge you already keep on your own computer. It combines exact-text retrieval, structural signals, semantic vector search, optional reranking, and source-aware context assembly.

The desktop app manages data roots, Runtime components, models, knowledge-base builds, live watch, queries, and result review. The optional MCP server exposes the same retrieval core to MCP-capable AI clients through two read-only tools.

## Download

Open the [latest release](https://github.com/EllisMorrow/OmniClip-RAG/releases/latest) and choose the package that matches your use case:

| Package label | Use it for |
| --- | --- |
| `WIN-EXE` | Normal Windows desktop use: configure, build, watch, search, and review results |
| `MCP-...-win64.zip` | Manual `stdio` setup for an MCP client |
| `.mcpb` | MCP Registry or MCPB-aware clients |

> The MCP package does not build or modify knowledge bases. Build the index with the Windows desktop app first.

## Highlights

- **Local-first storage** — indexes, configuration, models, logs, and Runtime payloads stay under the active local data root.
- **Markdown and Logseq awareness** — understands Markdown content plus common Logseq properties, block references, and embeds.
- **Hybrid retrieval** — combines SQLite FTS5, structure-aware scoring, LanceDB vector search, `BAAI/bge-m3`, and optional `BAAI/bge-reranker-v2-m3`.
- **PDF and Tika extensions** — maintains separate extension indexes and returns unified results with explicit source labels. The Tika catalog exposes 1,290 format entries; real compatibility still depends on the source file and parser.
- **Event-driven live watch** — reacts to real file changes, waits for the configurable quiet period, and performs incremental updates in disposable worker processes.
- **Visible query stages** — preparing, lexical recall, semantic recall, fusion, reranking, finalization, and context packing remain visible in the Query Console.
- **Resource-aware execution** — Auto mode prefers a usable NVIDIA CUDA path and falls back safely when Windows Commit, physical memory, or GPU memory is insufficient.
- **Read-only MCP access** — MCP clients can inspect readiness and search, but cannot build indexes, delete data, or change configuration.

## Privacy boundary

OmniClip RAG is local-first, not a promise that every possible workflow is permanently offline.

- Source files are read for indexing; normal build and source-removal operations do not delete or rewrite the original notes or documents.
- Network access is needed only for user-initiated downloads such as Runtime components, models, Java, or Tika.
- Retrieved text leaves the app only when you copy it, export it, or explicitly let an MCP client request it.
- Third-party AI clients and anything you paste into them remain subject to their own privacy policies.

Keep backups of important source material and review retrieved context before using it in high-stakes work.

## Quick start

1. Download the release asset whose name contains `WIN-EXE`.
2. Extract the archive and launch `OmniClipRAG.exe`.
3. Wait for the automatic Runtime detection to finish.
4. Choose the local data root and add one or more Markdown knowledge-base directories.
5. Install or repair the CPU/CUDA Runtime and local models from the Runtime page when prompted.
6. Run a full build once.
7. Search from the Query Console. Enable live watch if you want later edits to enter the index automatically.

Detailed instructions: [Getting Started](https://github.com/EllisMorrow/OmniClip-RAG/wiki/Getting-Started).

## Practical requirements

- 64-bit Windows
- Enough disk space for the application, external Runtime, local models, and indexes
- Internet access for the initial Runtime/model downloads unless the payloads are installed manually
- CPU execution is supported; a compatible NVIDIA GPU and driver are optional
- Windows automatic paging-file management is recommended for large knowledge bases and local AI models

The Runtime and models are external sidecars so healthy components can be reused across application versions. See [Runtime and Models](https://github.com/EllisMorrow/OmniClip-RAG/wiki/Runtime-and-Models).

## Retrieval flow

```mermaid
flowchart LR
    A["Markdown / PDF / Tika sources"] --> B["Local parsing and indexes"]
    B --> C["FTS5 lexical recall"]
    B --> D["LanceDB semantic recall"]
    C --> E["Fusion and filters"]
    D --> E
    E --> F["Optional reranking"]
    F --> G["Results and context pack"]
    G --> H["Clipboard or read-only MCP"]
```

## MCP in one minute

1. Build at least one knowledge base with the desktop app.
2. Download the MCP ZIP for manual `stdio` configuration, or use the Registry/MCPB package.
3. Point a compatible client at `OmniClipRAG-MCP.exe`.
4. Let the client call:
   - `omniclip.status` to inspect readiness and active retrieval mode
   - `omniclip.search` to retrieve source-labelled results

Some clients do not accept local `stdio` servers directly and may require a local proxy. See the [MCP Integration Wiki](https://github.com/EllisMorrow/OmniClip-RAG/wiki/MCP-Integration) and [MCP_SETUP.md](MCP_SETUP.md).

## Documentation

### User guides

| Topic | Guide |
| --- | --- |
| Installation and first build | [Getting Started](https://github.com/EllisMorrow/OmniClip-RAG/wiki/Getting-Started) |
| Runtime, models, CPU, and CUDA | [Runtime and Models](https://github.com/EllisMorrow/OmniClip-RAG/wiki/Runtime-and-Models) |
| Knowledge bases and live watch | [Knowledge Bases and Live Watch](https://github.com/EllisMorrow/OmniClip-RAG/wiki/Knowledge-Bases-and-Live-Watch) |
| Query stages and resource messages | [Search and Resources](https://github.com/EllisMorrow/OmniClip-RAG/wiki/Search-and-Resources) |
| PDF and Tika sources | [PDF and Tika](https://github.com/EllisMorrow/OmniClip-RAG/wiki/PDF-and-Tika) |
| MCP clients and tools | [MCP Integration](https://github.com/EllisMorrow/OmniClip-RAG/wiki/MCP-Integration) |
| Common problems | [Troubleshooting](https://github.com/EllisMorrow/OmniClip-RAG/wiki/Troubleshooting) |

### Project references

- [Architecture](ARCHITECTURE.md)
- [Runtime setup reference](RUNTIME_SETUP.md)
- [MCP setup reference](MCP_SETUP.md)
- [Changelog](CHANGELOG.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)
- [Release history](https://github.com/EllisMorrow/OmniClip-RAG/releases)

## Development

```powershell
# Desktop GUI
.\scripts\run_gui.ps1

# CLI status and query
.\scripts\run.ps1 status
.\scripts\run.ps1 query "your question"

# Build Windows packages
.\scripts\build_exe.ps1

# MCP self-check from source
python launcher_mcp.py --mcp-selfcheck
```

Developer orientation and architecture links live in [Development and Architecture](https://github.com/EllisMorrow/OmniClip-RAG/wiki/Development-and-Architecture).

## License and third-party software

OmniClip RAG is released under the [MIT License](LICENSE). It relies on open-source projects including PySide6, SQLite, LanceDB, Apache Arrow, PyTorch, sentence-transformers, Transformers, BGE models, PyPDF, Apache Tika, Eclipse Temurin, watchdog, PyInstaller, and the Model Context Protocol SDK.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the repository-level legal and distribution notices.

> OmniClip RAG and its retrieval results are provided without warranty. Verify important results, maintain backups, and do not use retrieved context or downstream AI output as the sole basis for medical, legal, financial, safety-critical, or other high-stakes decisions.
