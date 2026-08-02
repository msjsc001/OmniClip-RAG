# Caelune / 星野 v0.5.2 Release Notes

## Release Status

`v0.5.2` is the cross-device Runtime compatibility, source-state clarity, and Windows packaging reliability release.

## Release Focus

This release addresses failures that appeared only on some Windows devices while preserving existing knowledge bases, indexes, model caches, and query behavior. It also separates source-path health from index health so users can understand why an offline source may still be searchable from its retained local index.

## Highlights

- Fixed a CPU Runtime compatibility failure that could leave semantic retrieval usable but prevent `BAAI/bge-reranker-v2-m3` from running with `'dict' object has no attribute 'model_type'`.
- Added a targeted semantic-core repair path that updates the incompatible Transformers layer without forcing users to redownload models or rebuild indexes.
- Added Runtime package versions, system-memory snapshots, configured/selected knowledge-base counts, and active-scope state to query diagnostics for cross-device troubleshooting.
- Kept the active Markdown knowledge base aligned with saved and selected knowledge bases, preventing the Start page from incorrectly reporting that no knowledge base was selected.
- Markdown, PDF, and Tika rows now report source-path state and index state separately. A missing source with a retained index is shown as offline but still searchable.
- Disabled preflight, rebuild, and new live-watch actions for unavailable source paths while preserving safe removal and retained-index search behavior.
- Fixed an extension-source deletion reload race that could restore a deleted PDF/Tika source from stale registry state.
- Added a portable Shiboken recovery payload and early Runtime repair hook so Windows packages can recover when extraction or copy tools omit the nested `shiboken6` directory.
- Version references, Windows examples, MCPB metadata, and Registry metadata are aligned to `0.5.2`.

## Compatibility

- Existing data roots, Markdown/PDF/Tika indexes, model caches, Runtime components, live-watch settings, and UI settings remain compatible.
- No model redownload or index rebuild is required solely for this update.
- Devices with the identified semantic-core incompatibility can use the targeted repair action, then refresh Runtime detection and restart Caelune.
- The Python package remains `omniclip_rag` and the MCP tool IDs remain `omniclip.status` and `omniclip.search`.

## Source Removal and Cleanup

- Removing a Markdown knowledge base from the saved list keeps its independent workspace and index for safe recovery.
- Re-adding the exact same source path reconnects to that retained workspace.
- The Data-page index cleanup clears the current workspace's Markdown SQLite and LanceDB indexes; it does not remove the entire workspace or its isolated PDF/Tika indexes.
- PDF/Tika row deletion removes that source and clears only its matching extension index/state.
- Original source notes and documents are never deleted by these actions.

See the [Knowledge Bases and Live Watch](https://github.com/EllisMorrow/Caelune/wiki/Knowledge-Bases-and-Live-Watch) Wiki page for the complete bilingual explanation.

## Release Assets

- `Caelune-v0.5.2-WIN-EXE.zip`
  - ordinary Windows desktop GUI package
- `Caelune-MCP-v0.5.2-win64.zip`
  - manual MCP package for direct `stdio` setup
- `caelune-mcp-win-x64-v0.5.2.mcpb`
  - MCPB package for Registry and MCPB-aware clients

## Validation Scope

- Full automated test suite: 441 tests passed.
- Focused regressions cover Runtime repair, active knowledge-base recovery, source-path/index-state presentation, extension deletion, reranker compatibility classification, and Shiboken packaging fallback.
- Clean PyInstaller builds for the GUI and MCP executables.
- User verification of the packaged v0.5.2 desktop executable on the affected Windows workflow.
- MCPB schema validation, packing, unpacking, and entry-point verification.
- Release-archive integrity, required-file, privacy, and SHA-256 checks.

## Privacy

- Release archives exclude local Runtime installations, model caches, knowledge-base files, indexes, logs, configuration files, credentials, Registry tokens, temporary worker payloads, and local build paths.
- Query diagnostics may contain private paths and workspace names. Remove them before posting logs publicly.

## Upgrade Guidance

Extract `Caelune-v0.5.2-WIN-EXE.zip` to a new folder instead of overwriting an older release, then start `Caelune.exe`. Keep the previous application folder until the new build has opened normally, completed Runtime detection, and completed a query.
