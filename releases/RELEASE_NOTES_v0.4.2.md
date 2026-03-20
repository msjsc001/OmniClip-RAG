# OmniClip RAG v0.4.2 Release Notes

## Release Focus

`v0.4.2` turns the recent environment-root and GUI recovery work into a release-ready product line.

This version is not about adding a second new subsystem. It is about making the current desktop app, the MCP shell, and the startup/runtime/data-root contract behave like one coherent product instead of a collection of adjacent flows.

## Highlights

- Unified the active data root into one startup truth shared by GUI, launcher, headless bootstrap, Runtime selection, logs, model cache, and extension runtime storage.
- Added a restricted GUI recovery shell so users can still repair or switch environments when the active data root is unavailable, instead of being locked out by a broken path.
- Added a proper saved data-root switcher with preflight classification, environment summaries, invalid-path cleanup, and controlled restart semantics.
- Added compact query-desk collapse so the search surface can shrink to a one-line working mode when extra controls are not needed.
- Added five classic UI themes:
  - Sepia
  - Nord
  - Solarized Light
  - Solarized Dark
  - Graphite
- Unified the app icon chain so the runtime assets, packaged Windows icon resources, and GUI shell point at the same icon source.
- Published the project website on GitHub Pages:
  - [https://msjsc001.github.io/OmniClip-RAG/](https://msjsc001.github.io/OmniClip-RAG/)

## Release Assets

`v0.4.2` is designed to ship three release assets:

- `OmniClipRAG-v0.4.2-win64.zip`
  - desktop GUI package
- `OmniClipRAG-MCP-v0.4.2-win64.zip`
  - manual MCP package for direct `stdio` setup
- `omniclip-rag-mcp-win-x64-v0.4.2.mcpb`
  - MCPB package for the official MCP Registry line

## MCP Registry Status

This release refreshes the Registry metadata and the `.mcpb` package line for `v0.4.2`.

However, the actual MCP Registry publish should only happen **after** the final `.mcpb` asset is uploaded to the public GitHub Release for `v0.4.2`.

If the Release remains draft-only or has no public asset attached, `server.json` will point at a URL that public Registry validation cannot fetch.

## Notes

- The MCP line remains **read-only**.
- V1 still remains **stdio-only** and **tools-only**.
- Environment switching remains a **controlled restart** workflow; this release does not attempt full in-process hot migration.
