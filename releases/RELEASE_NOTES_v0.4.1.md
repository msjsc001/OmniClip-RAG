# OmniClip RAG v0.4.1 Release Notes

## Release Focus

`v0.4.1` turns the MCP line into a **Registry-ready release line**.

The desktop GUI and the read-only MCP shell were already stable in `v0.4.0`. This follow-up release finishes the packaging and documentation work needed to publish OmniClip RAG through the official MCP Registry instead of treating the MCP shell as only a manually shared ZIP.

## Highlights

- Added a formal root `server.json` for MCP Registry publishing.
- Added a Windows MCPB bundle route for `OmniClipRAG-MCP.exe`.
- Added a dedicated `scripts/build_mcpb.ps1` workflow that:
  - stages the packaged MCP shell,
  - writes and validates the MCPB manifest,
  - packs the final `.mcpb`,
  - computes `SHA256`,
  - unpacks the artifact again to verify the executable entry path,
  - regenerates `server.json` from the same metadata source.
- Updated MCP docs so Registry / MCPB users and manual ZIP users are guided down the correct path.
- Updated the MCP example configs and packaged MCP support files to `v0.4.1`.

## Release Assets

`v0.4.1` is designed to ship three MCP-relevant assets:

- `OmniClipRAG-v0.4.1-win64.zip`
  - desktop GUI package
- `OmniClipRAG-MCP-v0.4.1-win64.zip`
  - manual MCP package for direct `stdio` setup
- `omniclip-rag-mcp-win-x64-v0.4.1.mcpb`
  - official MCP Registry / MCPB package

## Why This Version Exists

The official MCP ecosystem no longer uses the old `modelcontextprotocol/servers` README as the primary discovery path for third-party servers. The correct long-term route is the **MCP Registry**.

Because Registry metadata becomes immutable once published, `v0.4.1` is intentionally reserved as the first Registry-facing OmniClip version instead of retrofitting the already public `v0.4.0`.

## Notes

- The MCP line remains **read-only**.
- V1 still remains **stdio-only** and **tools-only**.
- Registry publishing is intentionally manual for the first release; automation can come later after the first publish chain has been verified end-to-end.
