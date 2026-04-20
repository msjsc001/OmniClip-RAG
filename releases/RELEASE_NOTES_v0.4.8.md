# OmniClip RAG v0.4.8 Release Notes

## Release Focus

`v0.4.8` is the startup responsiveness and extension-source removal release.

This version keeps the current Runtime, Markdown, PDF, Tika, and MCP product lines intact, but closes two high-friction desktop issues that were still visible in normal use. First, packaged Windows builds no longer let heavy Runtime-management refreshes and extension-source summary rebuilds block the first paint of the main window. Second, the `拓展格式 > PDF / Tika` source-directory danger action now behaves like a real source removal flow instead of a half-step index cleanup: it clears the matching extension index/state data and then removes the source directory from the extension registry, while still never touching the user's original files.

## Highlights

- Packaged GUI startup now prioritizes first paint before background Runtime-management and extension-summary refresh work.
- Runtime-management state and extension-source summaries are now filled in asynchronously after the shell appears instead of blocking the main thread during startup replay.
- The PDF/Tika row-level danger action is now a true remove-source action rather than a “clear index only” action.
- Removing an extension source now:
  - clears the matching extension index/state data
  - removes the source directory from the extension registry/UI list
  - keeps the original external files untouched
- Cancelled source-delete tasks no longer accidentally drop the source from the extension registry.

## Release Assets

`v0.4.8` keeps the same three public release asset forms:

- `OmniClipRAG-v0.4.8-win64.zip`
  - desktop GUI package
- `OmniClipRAG-MCP-v0.4.8-win64.zip`
  - manual MCP package for direct `stdio` setup
- `omniclip-rag-mcp-win-x64-v0.4.8.mcpb`
  - MCPB package for the official MCP Registry line

## MCP / Registry Note

This release does not widen the MCP tool surface and does not change query semantics.

What changes is release-line consistency:

- desktop, MCP ZIP, and MCPB assets now line up on one `v0.4.8` version line
- local setup examples now point at the `v0.4.8` executable paths
- the project metadata line is internally consistent again, including `pyproject.toml`

## Notes

- This release does not change Runtime installation contracts.
- It does not change Markdown/PDF/Tika retrieval semantics.
- It does not delete user source folders when removing an extension source directory.
- The scope stays intentionally narrow: improve startup responsiveness and complete the PDF/Tika source-removal UX without widening product surface area.
