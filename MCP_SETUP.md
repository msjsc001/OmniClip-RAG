# OmniClip RAG MCP Setup

## What It Is

`OmniClip RAG MCP Server` is the read-only, headless MCP interface for OmniClip's local retrieval core.

V1 deliberately keeps the surface small and stable:

- transport: `stdio`
- tools only: `omniclip.status`, `omniclip.search`
- no GUI startup
- no write/build/delete/config operations
- no prompts/resources yet

The MCP server is a second shell over the same retrieval core. It is not a second backend.

## Delivery Shape

Packaged builds now ship in two separate forms:

- `OmniClipRAG.exe`
  - desktop GUI
  - windowed
- `OmniClipRAG-MCP.exe`
  - headless MCP server
  - console / stdio

Both share the same local data roots, Runtime context, and QueryService behavior.

## Tools

### `omniclip.status`

Use this first when a client wants to understand whether search is ready and whether the default mode is degraded.

The structured result includes at least:

- `version`
- `query_ready`
- `default_mode`
- `semantic_runtime_ready`
- `degraded_default`
- `device`
- `available_families`
- `data_root`
- `runtime_root`
- `runtime_preferred_root`
- `snapshot_id`
- `warnings`
- `last_selfcheck_ok`

### `omniclip.search`

Read-only local knowledge retrieval across:

- `markdown`
- `pdf`
- `tika`

Inputs:

- `query` (required)
- `allowed_families` (optional)
- `top_k` (default `5`, max `8`)
- `max_snippet_chars` (default `800`, max `1200`)

Outputs always include both:

- `structuredContent`
- `content`

The server also makes degradation explicit:

- `effective_mode = hybrid | lexical_only`
- `degraded = true | false`
- `warnings = [...]`

## Headless Self-Check

### From source

```powershell
python launcher_mcp.py --mcp-selfcheck
```

### From packaged MCP build

```powershell
.\OmniClipRAG-MCP.exe --mcp-selfcheck
```

This writes a shared self-check record under:

```text
%APPDATA%\OmniClip RAG\shared\mcp_selfcheck.json
```

The self-check is meant to verify:

- headless bootstrap works
- Runtime can be seen
- QueryService can be created
- status can be returned
- a minimal search can run

## Runtime and Data Paths

The MCP server follows the same storage rules as the desktop app:

- shared app data lives under `%APPDATA%\OmniClip RAG\shared`
- per-workspace data remains workspace-scoped
- Runtime prefers the shared AppData sidecar root
- healthy legacy Runtime folders may still be reused

The MCP server is strictly read-only, but it can still report degraded search if Runtime or local semantic dependencies are incomplete.

## Logging Rules

For stdio MCP compatibility:

- `stdout` is reserved for MCP protocol traffic
- logs and crash details go to `stderr` and the normal file-log system

Do not add plain `print()` diagnostics to MCP request handling.

## Example Client Configurations

Example config files live under:

- `examples/mcp/claude_desktop.json`
- `examples/mcp/cursor.json`
- `examples/mcp/cline.json`

Replace the executable path with the actual location of `OmniClipRAG-MCP.exe` on your machine.

## Jan.ai Quick Setup

Jan.ai has already been tested successfully with OmniClip's packaged MCP build.

Use these values when adding a new MCP server in Jan.ai:

- `Server Name`: `OmniClip RAG`
- `Transport Type`: `STDIO`
- `Command`: full path to `OmniClipRAG-MCP.exe`
- `Arguments`: leave empty
- `Environment Variables`: leave empty unless you intentionally overrode your data root or runtime root

Example:

```text
D:\software\OmniClip RAG\dist\OmniClipRAG-MCP-v0.4.0\OmniClipRAG-MCP.exe
```

## How To Ask Your AI

After the MCP server is connected, you can speak to the AI naturally. Good examples:

- `Use OmniClip to search my local knowledge base for "project roadmap" and summarize the useful parts.`
- `Call omniclip.status first and tell me whether my local vault is ready.`
- `Search only PDF results in OmniClip for "attention mechanism".`
- `Find notes about "my thinking model" in OmniClip and show me the top 5 snippets with sources.`

The most important habit is to tell the AI:

- what topic you want
- whether you only want `markdown`, `pdf`, or `tika`
- whether you want short answers or source-backed snippets

## Important Reminder

`OmniClipRAG-MCP.exe` is read-only. It does not build indexes, delete data, or change your configuration.

You should still use the desktop app to:

- build or rebuild indexes
- manage Runtime
- configure extension formats
- maintain your local knowledge base setup

## Notes

- V1 is intentionally `stdio`-only.
- V1 is intentionally `tools`-only.
- V1 intentionally does not expose index building, cleanup, file mutation, or config mutation.
- Future `Streamable HTTP` support is planned as a separate follow-up line rather than bolted onto this first transport.
