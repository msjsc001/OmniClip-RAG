# OmniClip RAG v0.4.7 Release Notes

## Release Focus

`v0.4.7` is the Runtime hardening and local semantic model compatibility release.

This version keeps the lightweight desktop-EXE plus external-Runtime architecture, but removes several pieces of user-environment fragility from that flow. Runtime installation is now driven by the packaged bundled-Python path and full wheel manifests instead of relying on whatever Python or pip state the machine happens to have. In parallel, local `BAAI/bge-m3` loading is hardened against a real `transformers 4.57.2` tokenizer metadata bug that could block vector initialization on some machines even after the model had downloaded successfully.

## Highlights

- Runtime installation no longer treats system Python as a normal-user prerequisite.
- Runtime component installs now use locked wheel manifests instead of drift-prone live dependency resolution.
- Long Runtime downloads and installs now expose real stage progress:
  - manifest load
  - wheel download
  - SHA verification
  - offline install
  - Runtime validation
- CPU `vector-store` Runtime no longer fails because of the stale `onnxruntime==1.24.0` pin.
- Local `BAAI/bge-m3` snapshots now get automatic metadata repair for the `transformers 4.57.2` local tokenizer bug before the build gives up.
- CUDA components remain optional on non-NVIDIA machines, but can now still be downloaded or repaired manually for installation-chain testing without being misreported as usable GPU acceleration.

## Release Assets

`v0.4.7` ships the same three public release assets:

- `OmniClipRAG-v0.4.7-win64.zip`
  - desktop GUI package
- `OmniClipRAG-MCP-v0.4.7-win64.zip`
  - manual MCP package for direct `stdio` setup
- `omniclip-rag-mcp-win-x64-v0.4.7.mcpb`
  - MCPB package for the official MCP Registry line

## MCP / Registry Note

This release does not widen the MCP tool surface. The MCP line stays intentionally small and read-only.

What changes is release integrity:

- the Registry-facing `.mcpb` artifact is rebuilt from the same tagged source
- the desktop GUI ZIP, MCP ZIP, and Registry metadata are kept on one `v0.4.7` line
- example client configs and setup instructions now point at the `v0.4.7` paths

## Notes

- This release does not turn Runtime into a monolithic prebuilt payload package.
- It does not change Markdown / PDF / Tika retrieval contracts.
- It does not change MCP query semantics.
- The scope is narrower and safer: make Runtime bring-up and local semantic initialization substantially more robust without widening the product surface.
