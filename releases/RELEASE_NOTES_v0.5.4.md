# Caelune / 凯露恩 v0.5.4 Release Notes

## Release Status

`v0.5.4` is the live-watch responsiveness, multi-knowledge-base fairness, and visible backlog-progress release.

## Release Focus

This release makes incremental Markdown indexing react to the notes users are editing now, while still safely catching up with changes made while Caelune was closed. Large backlogs are split into fair, efficient turns and report recalculated completion estimates without weakening the existing resource limits.

## Highlights

- Changes made while Caelune was closed are detected when live watch starts and are processed in bounded batches.
- Fresh editor events take priority over historical reconciliation and retained repair work.
- Multi-knowledge-base semantic indexing now uses a fair FIFO gate so one busy source cannot repeatedly monopolize the worker.
- Batches larger than three files amortize model-process startup cost while retaining the configured vector encode, RAM, and VRAM limits.
- The Activity Log identifies live updates, offline reconciliation, repair work, retries, progress, and completion.
- Backlogs containing more than three files show a per-knowledge-base ETA that is recalculated from completed end-to-end batches, including time spent waiting for a fair worker turn.
- Stale repair paths are removed, and valid historical repairs are consumed in bounded pieces after current edits.

## Compatibility

- Existing knowledge bases, Markdown/PDF/Tika indexes, Runtime components, model caches, live-watch settings, and UI settings remain compatible.
- No model redownload, Runtime reinstall, or full index rebuild is required solely for this update.
- The Python package remains `omniclip_rag`, and the MCP tool IDs remain `omniclip.status` and `omniclip.search`.

## Release Assets

- `Caelune-v0.5.4-WIN-EXE.zip`
  - ordinary Windows desktop GUI package
- `Caelune-MCP-v0.5.4-win64.zip`
  - manual MCP package for direct `stdio` setup
- `caelune-mcp-win-x64-v0.5.4.mcpb`
  - MCPB package for Registry and MCPB-aware clients

## Validation Scope

- Full automated test suite: 452 tests passed.
- Focused regressions cover Watchdog add/modify/delete events, offline restart reconciliation, live-event priority, bounded repair work, fair repeated multi-vault turns, dynamic ETA updates, and UI log rendering.
- Clean PyInstaller builds for the GUI and MCP executables.
- Windows archive integrity and Shiboken portability checks.
- MCPB schema validation, packing, unpacking, and entry-point verification.
- Release-bundle privacy scan for local user paths, knowledge-base names, Runtime/model data, indexes, logs, configuration, credentials, and temporary worker payloads.

## Privacy

- Release archives exclude local Runtime installations, model caches, knowledge-base files, indexes, logs, configuration files, credentials, Registry tokens, temporary worker payloads, and local build paths.
- Activity logs may contain private paths and knowledge-base names. Remove them before posting logs publicly.

## Upgrade Guidance

Extract `Caelune-v0.5.4-WIN-EXE.zip` to a new folder instead of overwriting an older release, then start `Caelune.exe`. Keep the previous application folder until the new build has opened normally, completed Runtime detection, and verified live-watch processing on a small edit.
