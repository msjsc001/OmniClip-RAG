# Caelune / 凯露恩 v0.5.0 Release Notes

## Release Status

`v0.5.0` is the product-identity migration release from OmniClip RAG / 方寸引 to **Caelune / 凯露恩**.

## Release Focus

This release gives the desktop app, Windows executables, MCP package, documentation, and website one consistent public identity. The migration is deliberately compatibility-first: existing users keep their selected data root, local indexes, downloaded Runtime, configuration, and MCP tool contracts.

## Highlights

- The desktop title and public English product name are now **Caelune**.
- The Chinese product name is now **凯露恩**.
- New Windows executables are named `Caelune.exe` and `Caelune-MCP.exe`.
- Fresh installations use `%APPDATA%\Caelune-default` and a `%APPDATA%\Caelune\bootstrap.json` pointer.
- New configuration overrides use the `CAELUNE_*` prefix.
- The website, README files, current setup guides, package metadata, and MCP Registry metadata use the new identity.

## Compatibility

- Existing `%APPDATA%\OmniClip RAG` environments remain recognized and are reused automatically.
- Existing bootstrap pointers, Runtime directories, and `OMNICLIP_*` environment overrides remain supported as legacy aliases.
- The Python import package remains `omniclip_rag` to avoid breaking internal imports and integrations.
- MCP tool IDs remain `omniclip.status` and `omniclip.search` so existing AI-client configurations do not break.
- The MCPB package identity is now `io.github.EllisMorrow/caelune-mcp`; users of the pre-Caelune legacy MCPB should remove it once before installing the renamed package.
- Stored SQLite, vector-index, marker, and log filenames are not rewritten or migrated destructively.

## Release Assets

- `Caelune-v0.5.0-WIN-EXE.zip`
  - ordinary Windows desktop GUI package
- `Caelune-MCP-v0.5.0-win64.zip`
  - manual MCP package for direct `stdio` setup
- `caelune-mcp-win-x64-v0.5.0.mcpb`
  - MCPB package for Registry and MCPB-aware clients

## Privacy

- Release archives exclude local Runtime installations, model caches, knowledge-base files, indexes, logs, configuration files, credentials, Registry tokens, and temporary worker payloads.
- The rename does not upload, copy, or inspect user knowledge-base content.

## Upgrade Guidance

Extract the new package to a new folder and start `Caelune.exe`. The app will discover the previous data-root selection and Runtime when present. Keep the previous application folder until the first Caelune launch and a normal query have been verified; it can then be archived or removed manually.
