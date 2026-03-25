# OmniClip RAG v0.4.5 Release Notes

## Release Focus

`v0.4.5` is the extension oversized-routing hardening release.

This version finishes a practical stability problem that real users hit during `PDF / Tika` builds: some extension files are valid large text carriers and should still enter the search index, while others are really structured noise explosions that should never be allowed to poison the index or pin a build at a fake-progress plateau.

The release turns that distinction into a stable contract.

## Highlights

- Oversized extension inputs are no longer handled with one blunt rule:
  - text-heavy carriers such as `PDF / DOC / DOCX / EPUB / TXT / RTF / ODT / EML / MSG` now regroup oversized parse output before indexing
  - structured/noisy carriers such as `HTML / MHTML / XML / XLSX / PPTX / ZIP` are skipped before they can drag builds into long vector-write stalls
- Extension builds now leave a durable per-file issue report:
  - regrouped oversized text carriers
  - structured oversized skips
  - parse/runtime/I/O failures
- Activity feedback is more honest without becoming noisy:
  - the desktop shell reports grouped summaries
  - the file-level reasons go into a dedicated issue log instead of flooding the activity area
- The practical `Tika at 92%` stall class is narrowed significantly:
  - oversized structured pages now exit before `write_vector`
  - oversize text carriers are retained through regrouping instead of being silently thrown away

## Release Assets

`v0.4.5` ships the same three public release assets:

- `OmniClipRAG-v0.4.5-win64.zip`
  - desktop GUI package
- `OmniClipRAG-MCP-v0.4.5-win64.zip`
  - manual MCP package for direct `stdio` setup
- `omniclip-rag-mcp-win-x64-v0.4.5.mcpb`
  - MCPB package for the official MCP Registry line

## MCP / Registry Note

This release does **not** add new MCP tools or widen the MCP surface.

What changes is release alignment:

- Registry metadata is refreshed to `v0.4.5`
- the `.mcpb` asset is rebuilt from the same tagged source
- the MCP ZIP, GUI ZIP, and Registry-facing metadata stay on the same release line

## Notes

- This release intentionally does **not** optimize or replace the underlying parsers.
- It also does **not** redesign extension query semantics, resume semantics, or index-state contracts.
- The goal is narrower and safer: keep valid large text carriers searchable, keep structured noise out of the indexes, and make the reasons auditable after every build.
