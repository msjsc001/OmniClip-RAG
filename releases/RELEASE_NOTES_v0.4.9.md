# OmniClip RAG v0.4.9 Release Notes

## Release Focus

`v0.4.9` completes the Windows startup-responsiveness work and refreshes the application identity with a cleaner flat icon.

The first window now appears without parsing the full Tika format catalog or constructing the complete query service in the GUI process. Immediately after first paint, the Runtime status changes to `detecting` and an isolated child process performs the real validation. The initial workspace snapshot follows in another isolated process, while Markdown, extension-source, and Tika refreshes are deliberately staggered so background work does not overwhelm the interface.

## Highlights

- Runtime detection starts automatically after first paint.
- The Start and Runtime pages show a truthful `runtime detecting…` state and update automatically when validation finishes.
- Runtime validation and initial workspace status collection run outside the GUI process.
- Markdown status, extension-source summaries, and Tika status start in a controlled sequence instead of competing at once.
- Full Tika JAR format-catalog parsing is deferred until the user opens the format picker.
- Complete-but-unverified Runtime installations are shown as installed rather than missing.
- Enabled PDF/Tika sources distinguish unbuilt state from pending status confirmation.
- The new flat ring-and-spark application icon stays clear at Windows thumbnail sizes.

## Release Assets

`v0.4.9` ships the same three public release asset forms:

- `OmniClipRAG-v0.4.9-win64.zip`
  - desktop GUI package
- `OmniClipRAG-MCP-v0.4.9-win64.zip`
  - manual MCP package for direct `stdio` setup
- `omniclip-rag-mcp-win-x64-v0.4.9.mcpb`
  - MCPB package for the official MCP Registry line

## MCP / Registry Note

This release does not add MCP tools or change query semantics.

- The desktop ZIP, MCP ZIP, MCPB package, and Registry metadata share the same `v0.4.9` version.
- Public repository and release URLs now use the canonical `EllisMorrow/OmniClip-RAG` path.
- The Registry server name remains `io.github.msjsc001/omniclip-rag-mcp` so this version updates the existing official entry instead of creating a duplicate server identity.

## Validation

- Relevant startup, Runtime, extension-status, desktop-entry, localization, and MCP Registry tests pass.
- A packaged Windows startup smoke test observed no non-responsive GUI samples while Runtime detection was active.
- Release bundles are audited to exclude local workspaces, indexes, logs, caches, Runtime installations, and heavyweight semantic packages.

## Notes

- Runtime installation, Markdown/PDF/Tika retrieval, and MCP tool contracts are unchanged.
- Runtime detection may still take several seconds on a cold machine, but it no longer performs heavy validation inside the GUI process.
- On a machine below the safe resource threshold, automatic Runtime validation is skipped rather than forcing background work that could degrade the interface.
