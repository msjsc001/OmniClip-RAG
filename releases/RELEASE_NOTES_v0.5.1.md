# Caelune / 星野 v0.5.1 Release Notes

## Release Status

`v0.5.1` is the desktop UI clarity, onboarding, and release-metadata refinement release.

## Release Focus

This release makes Caelune easier to read and operate without changing its local-first retrieval architecture, knowledge-base formats, Runtime layout, or MCP tool contracts. The desktop interface now has a clearer visual hierarchy across its supported themes, more practical bilingual guidance, and a startup experience that follows the saved application theme from the first visible window.

## Highlights

- Refined the main navigation, cards, tables, buttons, input fields, focus states, status badges, and header hierarchy across the supported UI themes.
- Improved Chinese and English labels, guidance, and tooltips for first-time users while keeping expert workflows compact.
- The startup progress window now loads the saved UI theme before it is shown, avoiding a visible Windows-theme-to-app-theme switch.
- The Windows title bar now shows the release version instead of repeating the application name.
- The README files now show separate, current, full-resolution Query Console and Results and Details screenshots in Chinese and English.
- MCP initialization now reports the Caelune release version instead of the bundled MCP SDK version.
- Version references, Windows examples, MCPB metadata, and Registry metadata are aligned to `0.5.1`.

## Compatibility

- Existing data roots, knowledge bases, indexes, Runtime components, model caches, UI settings, and live-watch settings remain compatible.
- The Python package remains `omniclip_rag` and the MCP tool IDs remain `omniclip.status` and `omniclip.search`.
- No database migration, index rebuild, Runtime reinstall, or model redownload is required solely for this update.

## Release Assets

- `Caelune-v0.5.1-WIN-EXE.zip`
  - ordinary Windows desktop GUI package
- `Caelune-MCP-v0.5.1-win64.zip`
  - manual MCP package for direct `stdio` setup
- `caelune-mcp-win-x64-v0.5.1.mcpb`
  - MCPB package for Registry and MCPB-aware clients

## Validation Scope

- Full automated test suite plus focused Qt identity and MCP release-version regressions.
- Clean PyInstaller builds for the GUI and MCP executables.
- Packaged GUI title-bar smoke test on Windows 11.
- Valid MCP `initialize` request against the packaged executable.
- MCPB schema validation, packing, unpacking, and entry-point verification.
- Release-archive path, privacy, dependency-purity, and SHA-256 checks.

## Privacy

- Release archives exclude local Runtime installations, model caches, knowledge-base files, indexes, logs, configuration files, credentials, Registry tokens, temporary worker payloads, and local build paths.
- Documentation screenshots contain no query results, note contents, usernames, credentials, or absolute local paths.

## Upgrade Guidance

Extract `Caelune-v0.5.1-WIN-EXE.zip` to a new folder and start `Caelune.exe`. Keep the previous application folder until the new build has opened normally and completed a query; it can then be archived or removed manually.
