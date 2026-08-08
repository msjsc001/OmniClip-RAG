# Caelune / 凯露恩 v0.5.6 Release Notes

## Release Status

`v0.5.6` is a focused result-metadata correctness release.

## Release Focus

This release fixes a cross-encoder reranking path that rebuilt search results without carrying forward extension-source metadata. Reranked PDF and Tika results now retain the source information needed by the desktop app and MCP output.

## Highlights

- PDF results keep their source family, file kind, display label, and page number after reranking.
- Tika-backed results keep their source family, file kind, and display label after reranking.
- Reranking still changes only relevance scores and ordering; scoring weights and model behavior are unchanged.
- Regression coverage protects both PDF and Tika metadata.

## Compatibility

- Existing knowledge bases, Markdown/PDF/Tika indexes, Runtime components, model caches, configuration, and UI settings remain compatible.
- No model redownload, Runtime reinstall, or index rebuild is required solely for this update.
- The Python package remains `omniclip_rag`, and the MCP tool IDs remain `omniclip.status` and `omniclip.search`.

## Release Assets

- `Caelune-v0.5.6-WIN-EXE.zip`
  - ordinary Windows desktop GUI package
- `Caelune-MCP-v0.5.6-win64.zip`
  - manual MCP package for direct `stdio` setup
- `caelune-mcp-win-x64-v0.5.6.mcpb`
  - MCPB package for Registry and MCPB-aware clients

## Validation Scope

- Full automated test suite: 453 tests passed.
- Focused regression coverage for preserving PDF and Tika metadata through cross-encoder reranking.
- Clean PyInstaller builds for the GUI and MCP executables.
- Windows archive integrity and packaged-file privacy audits.
- MCPB schema validation, packing, unpacking, entry-point verification, and Registry SHA256 regeneration.
- Packaged MCP entry-point and CLI argument smoke checks without reading a configured knowledge base.

## Privacy

- Release archives exclude local Runtime installations, model caches, knowledge-base files, indexes, logs, configuration files, credentials, Registry tokens, temporary worker payloads, and local build paths.
- Activity logs and MCP self-check output may contain private paths, knowledge-base names, or retrieved snippets. Review and remove them before sharing diagnostics publicly.

## Upgrade Guidance

Extract `Caelune-v0.5.6-WIN-EXE.zip` to a new folder instead of overwriting an older release, then start `Caelune.exe`. Keep the previous application folder until the new build has opened normally and completed Runtime detection.
