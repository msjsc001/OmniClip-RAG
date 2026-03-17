# OmniClip RAG / 方寸引 v0.4.0

## Summary

`v0.4.0` turns OmniClip from a local desktop retrieval tool into a dual-shell product with a standard read-only MCP interface.

This release does **not** bolt MCP onto the GUI executable as a convenience flag. Instead, it introduces a second, headless shell over the same retrieval core:

- `OmniClipRAG.exe` for desktop GUI
- `OmniClipRAG-MCP.exe` for MCP stdio integration

The goal is to let MCP-capable AI clients use OmniClip's local knowledge retrieval safely, while preserving the existing GUI behavior and keeping the backend single-sourced.

## Highlights

- added a dedicated **headless MCP executable** instead of reusing the windowed GUI build
- kept MCP V1 intentionally small and stable with only two read-only tools:
  - `omniclip.status`
  - `omniclip.search`
- introduced a **shared headless bootstrap** so GUI and MCP now align on the same RuntimeContext, DataPaths, and QueryService startup path
- made MCP search explicitly **degradation-aware**
  - semantic path available -> `hybrid`
  - semantic path unavailable -> `lexical_only`
  - warnings are surfaced instead of hidden
- documented setup and client examples for Claude Desktop, Cursor, and Cline

## Included In This Release

### Shared core

- added a reusable headless bootstrap layer
- extracted launcher bootstrapping into shared helpers
- kept GUI and MCP as separate shells over the same retrieval kernel

### MCP server

- added `omniclip_rag/mcp/core.py`
- added `omniclip_rag/app_entry/mcp.py`
- added `launcher_mcp.py`
- added `omniclip.status`
- added `omniclip.search`
- added `--mcp-selfcheck`

### Packaging

- added `OmniClipRAG-MCP.spec`
- updated `build.py` to produce both:
  - `OmniClipRAG-v0.4.0/`
  - `OmniClipRAG-MCP-v0.4.0/`
- kept both packages lean and free of runtime payloads, model caches, and user data

### Documentation

- added `MCP_SETUP.md`
- added `examples/mcp/*.json`
- added plain-language README onboarding, including a tested Jan.ai `stdio` configuration example
- added the in-repo execution record:
  - `plans/OmniClip RAG MCP接入实施计划.md`
- updated README, README.zh-CN, ARCHITECTURE, and CHANGELOG for the MCP line

## V1 Boundary

This release intentionally does **not** include:

- write operations
- build/delete/config mutation through MCP
- prompts/resources
- Streamable HTTP
- remote auth / multi-tenant design

The first goal is a durable, read-only, local MCP interface over the existing retrieval core.
