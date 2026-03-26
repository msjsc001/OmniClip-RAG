# OmniClip RAG v0.4.6 Release Notes

## Release Focus

`v0.4.6` is the Markdown multi-vault cognition and UI navigation polish release.

This version does not widen the retrieval core or change the underlying multi-vault data model. Instead, it tightens the user-facing layer so the software finally speaks in the same language as the architecture that already exists: one primary Markdown vault, a separate included search/build scope, a more truthful quick-start flow, and fast theme-aware hover help for the places users most often hesitate.

## Highlights

- The configuration tabs now follow the real workflow order:
  - `开始 -> 设置 -> Runtime -> 拓展格式 -> 检索强化 -> UI -> 数据`
- The Markdown source-directory table now uses clearer wording:
  - `主库`
  - `纳入范围`
- The desktop shell now has a unified hover-help layer:
  - theme-aware tooltip styling
  - very fast display
  - one global on/off switch under `UI`
- The in-app quick-start and guide copy now describe the current product truth:
  - data root first
  - then Markdown source directories
  - then Runtime/model readiness
  - then preflight/build/search

## Release Assets

`v0.4.6` ships the same three public release assets:

- `OmniClipRAG-v0.4.6-win64.zip`
  - desktop GUI package
- `OmniClipRAG-MCP-v0.4.6-win64.zip`
  - manual MCP package for direct `stdio` setup
- `omniclip-rag-mcp-win-x64-v0.4.6.mcpb`
  - MCPB package for the official MCP Registry line

## MCP / Registry Note

This release does **not** add new MCP tools or widen the MCP surface.

What changes is release alignment:

- Registry metadata is refreshed to `v0.4.6`
- the `.mcpb` artifact is rebuilt from the same tagged source
- the desktop GUI ZIP, MCP ZIP, and Registry-facing metadata stay on one version line

## Notes

- This release intentionally does **not** redesign Markdown multi-vault scheduling semantics.
- It does **not** change extension index-state contracts, resume behavior, or deletion behavior.
- The goal is narrower and safer: make the software easier to understand and navigate without changing the underlying retrieval rules that were stabilized in the previous releases.
