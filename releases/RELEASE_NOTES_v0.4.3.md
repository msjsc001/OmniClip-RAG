# OmniClip RAG v0.4.3 Release Notes

## Release Focus

`v0.4.3` is the release that turns the recent hotfix train into a coherent public version.

The goal of this version is not to bolt on another big subsystem. The goal is to make the current desktop app, semantic retrieval stack, download/runtime flows, MCP shell, and release packaging line behave like one honest product surface.

## Highlights

- Semantic retrieval state is now explicit instead of silently misleading:
  - disabled semantic backend is surfaced as disabled
  - missing semantic vector tables are surfaced as rebuild-required
  - MCP degraded-mode payloads now match the desktop truth
- Automatic model and reranker downloads now follow the active data root end to end:
  - target directory
  - HF cache root
  - download logs
  - delete-model actions
- Automatic download visibility is much stronger:
  - dedicated worker process
  - visible terminal output
  - heartbeat progress lines
  - China-first source chain: `ModelScope -> HF mirror -> Hugging Face official`
- The formal MCP release chain is back inside the repository:
  - `OmniClipRAG-MCP.spec` is now part of source control again
  - `build.py` can rebuild the GUI ZIP, MCP ZIP, and `.mcpb` from one version source of truth
- Public-doc hygiene was tightened before release:
  - tracked personal absolute paths were sanitized out of release-facing documentation

## Release Assets

`v0.4.3` is designed to ship three release assets:

- `OmniClipRAG-v0.4.3-win64.zip`
  - desktop GUI package
- `OmniClipRAG-MCP-v0.4.3-win64.zip`
  - manual MCP package for direct `stdio` setup
- `omniclip-rag-mcp-win-x64-v0.4.3.mcpb`
  - MCPB package for the official MCP Registry line

## MCP Registry Status

This release refreshes the Registry metadata and the `.mcpb` package line for `v0.4.3`.

The actual Registry publish should still happen only **after** the final `.mcpb` asset has been uploaded to the public GitHub Release for `v0.4.3`.

## Notes

- The MCP line remains **read-only**.
- V1 remains **stdio-only** and **tools-only**.
- Environment switching still remains a **controlled restart** workflow.
