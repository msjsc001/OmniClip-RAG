# Caelune / 凯露恩 v0.5.5 Release Notes

## Release Status

`v0.5.5` is the Chinese product-name, repository-owner, documentation, and public-surface consistency release.

## Release Focus

This release aligns the Windows desktop app, source tree, documentation, website, Wiki, licensing, MCP setup guidance, and release metadata around the current Caelune / 凯露恩 product identity and the canonical `EllisMorrow` repository owner.

## Highlights

- The Chinese desktop title and all current Chinese product copy use **凯露恩**.
- README, website accessibility metadata, architecture guidance, runtime instructions, third-party notices, changelog entries, and retained release notes use the current product name.
- Copyright and current repository-owner references use **EllisMorrow**.
- Legacy MCP migration guidance no longer repeats a retired account identifier, while the stable `omniclip.status` and `omniclip.search` tool IDs remain unchanged.
- Both README files keep the self-updating GitHub Release badge as their only version indicator.
- GitHub Wiki and repository-page copy are prepared for the same public-name update.

## Compatibility

- Existing knowledge bases, Markdown/PDF/Tika indexes, Runtime components, model caches, live-watch settings, and UI settings remain compatible.
- No model redownload, Runtime reinstall, or full index rebuild is required solely for this update.
- The Python package remains `omniclip_rag`, and existing MCP tool calls remain compatible.

## Release Assets

- `Caelune-v0.5.5-WIN-EXE.zip`
  - ordinary Windows desktop GUI package
- `Caelune-MCP-v0.5.5-win64.zip`
  - manual MCP package for direct `stdio` setup
- `caelune-mcp-win-x64-v0.5.5.mcpb`
  - MCPB package for Registry and MCPB-aware clients

## Validation Scope

- Full automated test suite: 452 tests passed.
- Current source tree and current `v0.5.5` build directories contain no retired Chinese product-name or repository-owner strings.
- Clean PyInstaller builds for the GUI and MCP executables.
- Windows archive integrity and Shiboken portability checks.
- MCPB schema validation, packing, unpacking, entry-point verification, and Registry SHA256 regeneration.
- Packaged MCP entry point and CLI argument smoke check completed successfully without reading a configured knowledge base.

## Privacy

- Release archives exclude local Runtime installations, model caches, knowledge-base files, indexes, logs, configuration files, credentials, Registry tokens, temporary worker payloads, and local build paths.
- Activity logs and MCP self-check output may contain private paths, knowledge-base names, or retrieved snippets. Review and remove them before sharing diagnostics publicly.

## Upgrade Guidance

Extract `Caelune-v0.5.5-WIN-EXE.zip` to a new folder instead of overwriting an older release, then start `Caelune.exe`. Keep the previous application folder until the new build has opened normally and completed Runtime detection.
