# OmniClip RAG MCP Setup

## What It Is

`OmniClip RAG MCP Server` is the read-only, headless MCP interface for OmniClip's local retrieval core.

V1 deliberately keeps the surface small and durable:

- transport: `stdio`
- tools only: `omniclip.status`, `omniclip.search`
- no GUI startup
- no write/build/delete/config operations
- no prompts/resources yet

The MCP server is a second shell over the same retrieval core. It is not a second backend.

## Delivery Shape

Packaged releases now come in three distinct forms:

- `OmniClipRAG-vX.Y.Z-win64.zip`
  - desktop GUI package
  - use this when you want the full desktop app for indexing, Runtime management, and normal daily use
- `OmniClipRAG-MCP-vX.Y.Z-win64.zip`
  - manual MCP package
  - use this when you want to point a local MCP client directly at `OmniClipRAG-MCP.exe`
- `omniclip-rag-mcp-win-x64-vX.Y.Z.mcpb`
  - MCPB package for the official MCP Registry and MCPB-aware clients
  - use this when you want a standard Registry-friendly distribution artifact instead of a raw ZIP

## Which Package Should You Download?

Use this rule of thumb:

- want the desktop app: download the GUI ZIP
- want to manually configure Jan.ai / OpenClaw / Claude Desktop / Cursor / Cline: download the MCP ZIP
- want the Registry/MCPB route: use the `.mcpb` package

The ZIP packages stay useful for users who prefer manual file-based setup. The `.mcpb` package is the standard publishable asset for the MCP Registry line.

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

## Official MCP Registry / MCPB

OmniClip RAG now keeps a first-class Registry publishing line.

The Registry metadata lives in the repository root as:

- `server.json`

The standard publishable artifact is:

- `omniclip-rag-mcp-win-x64-vX.Y.Z.mcpb`

Important difference:

- the ZIP package is for people who manually point a client at `OmniClipRAG-MCP.exe`
- the `.mcpb` package is for Registry publishing and MCPB-aware distribution flows

### Maintainer Publish Order

If you maintain releases for the project, keep this exact order:

1. build the GUI ZIP, MCP ZIP, and `.mcpb`
2. calculate the `.mcpb` SHA256
3. upload all assets to a GitHub Release
4. make sure the Release is **public**, not Draft
5. confirm `server.json` points to the final public `.mcpb` URL and SHA256
6. publish to the MCP Registry

Do **not** try to publish the Registry entry against a Draft Release URL. The Registry validator will see a public `404` and reject the publish attempt.

### Publisher Note

The Registry publish tool should follow the official MCP Registry quickstart. Treat `mcp-publisher` as the official publisher binary/tooling line from the Registry docs instead of assuming it is an npm package name.

## Example Client Configurations

Example config files live under:

- `examples/mcp/claude_desktop.json`
- `examples/mcp/cursor.json`
- `examples/mcp/cline.json`
- `examples/mcp/openclaw.json`

Replace the executable path with the actual location of `OmniClipRAG-MCP.exe` on your machine.

## Jan.ai Reference Setup

Use these values when adding a new MCP server in Jan.ai:

- `Server Name`: `OmniClip RAG`
- `Transport Type`: `STDIO`
- `Command`: full path to `OmniClipRAG-MCP.exe`
- `Arguments`: leave empty
- `Environment Variables`: leave empty unless you intentionally overrode your data root or runtime root

Example:

```text
D:\Apps\OmniClip RAG\dist\OmniClipRAG-MCP-v0.4.5\OmniClipRAG-MCP.exe
```

## OpenClaw Reference Setup

OpenClaw usually reads MCP server definitions from its config file instead of a visual form.

Typical config location:

```text
%USERPROFILE%\.openclaw\openclaw.json
```

Add or merge an `mcpServers` block like this:

```json
{
  "mcpServers": {
    "omniclip-rag": {
      "transport": "stdio",
      "command": "D:\\Apps\\OmniClip RAG\\dist\\OmniClipRAG-MCP-v0.4.5\\OmniClipRAG-MCP.exe",
      "args": []
    }
  }
}
```

Important notes:

- do not replace your whole config if OpenClaw already has other settings
- only merge the `mcpServers.omniclip-rag` entry into the existing file
- restart OpenClaw or its gateway/runtime process after saving

If you use a custom data root or runtime root, add the required environment variables in the same MCP server block according to your local layout.

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
