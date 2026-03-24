# OmniClip RAG v0.4.4 Release Notes

## Release Focus

`v0.4.4` is the extension-format hardening release.

This version does not try to add another flashy subsystem. It finishes a less glamorous but more important job: making the `PDF / Tika` extension line behave like a real product surface instead of a fragile side branch. The focus is extension-build truthfulness, packaged-PDF stability, and user-visible progress that actually explains whether work is still happening.

## Highlights

- Extension-format build control is materially stronger:
  - dedicated extension build state
  - build lease / conflict protection
  - interruption awareness
  - conservative resume flow
  - explicit `READY / query_ready / vector_ready` truth contracts
- PDF preflight is now lightweight and much more honest:
  - exact page counts are kept
  - the preflight path now reads PDF structure/metadata instead of drifting into partial text extraction
  - single-file PDF preflight no longer behaves like a tiny hidden rebuild
- Extension observability is finally usable:
  - real preflight progress callbacks
  - clearer stage labels
  - busy/indeterminate fallback when the denominator is not trustworthy
  - current-file + elapsed-time feedback instead of long silent stalls
- The packaged EXE line is more reliable again:
  - the PDF parser bundle now carries the right imports/metadata closure
  - frozen preflight no longer rejects healthy PDF parsing just because `importlib.metadata.version('pypdf')` cannot see dist-info
- Public release docs were cleaned again:
  - local development absolute paths were replaced with neutral install examples before release

## Release Assets

`v0.4.4` ships the same three public release assets:

- `OmniClipRAG-v0.4.4-win64.zip`
  - desktop GUI package
- `OmniClipRAG-MCP-v0.4.4-win64.zip`
  - manual MCP package for direct `stdio` setup
- `omniclip-rag-mcp-win-x64-v0.4.4.mcpb`
  - MCPB package for the official MCP Registry line

## MCP / Registry Note

This release does **not** introduce a new MCP tool surface. The MCP shell remains read-only and keeps the same `omniclip.status` + `omniclip.search` contract.

What changes here is release alignment:

- Registry metadata is refreshed to `v0.4.4`
- the `.mcpb` asset is rebuilt from the same source version
- the MCP ZIP and GUI ZIP stay aligned with the same tagged release

## Notes

- The Markdown mainline build/query contract is intentionally untouched by this release.
- Extension hardening remains isolated to the extension subsystem and the desktop surfaces that present its progress.
- Full extension builds still do not use a blue/green atomic swap line; the contract remains truthful interruption handling rather than hidden old-index service.
