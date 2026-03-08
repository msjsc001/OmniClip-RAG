# Changelog

## V0.1.1 - 2026-03-08

### Added

- Added multi-vault switching with isolated per-vault workspaces.
- Added explicit separation between shared cross-vault data and vault-specific workspace data.
- Added auto-download / manual-download model selection with `hf-mirror.com` and Hugging Face fallback guidance.
- Added space-and-time prechecks that estimate both disk usage and first-run duration.
- Added persisted unfinished-build state, restart-time resume prompts, and real pause/resume for full rebuilds.
- Added live task progress, elapsed-time, and ETA feedback to the desktop task panel.
- Added regression tests for paused rebuild control in the service layer, vector layer, and GUI layer.
- Added [RELEASE_NOTES_v0.1.1](RELEASE_NOTES_v0.1.1.md) for the post-`v0.1.0` desktop hardening update.

### Changed

- Reworked the desktop app into a more newcomer-first bilingual workflow with clearer guidance and fuller runtime localization.
- Changed data layout so model cache and general logs live under `shared/`, while each vault keeps its own isolated workspace under `workspaces/<workspace-id>/`.
- Changed the preflight flow to explain both storage cost and estimated build/download time before heavy operations begin.
- Changed model setup prompts so users can choose automatic download or manual download instructions before the app starts pulling files.
- Changed model loading so a valid local model cache is treated as authoritative and is reused across query, rebuild, and bootstrap flows.
- Changed full rebuild execution to support interruption recovery, batch-based vector writes, and explicit desktop-side pause/resume control.
- Changed desktop task feedback so users see task state, elapsed time, ETA, and rebuild progress instead of waiting silently.

### Fixed

- Fixed unnecessary Hugging Face network calls during search or indexing when a complete local model cache already exists.
- Fixed rebuild failures caused by unreadable Markdown files by skipping unreadable files instead of aborting the whole run.
- Fixed rebuild crashes caused by duplicated Logseq `id:: UUID` values by demoting later duplicates instead of breaking SQLite insertion.
- Fixed stale or invalid split-pane state that could collapse parts of the desktop layout after launch.
- Fixed first-run friction where startup background work could collide with a user's first button click.
- Fixed repeated model-download prompting when the model was already present and passed local integrity checks.

## V0.1.0 - 2026-03-07

### Added

- Delivered the first desktop GUI workflow for configuration, preflight, model bootstrap, rebuild, query, hot watch, open-directory actions, and selective cleanup.
- Added dual parsing support for standard Markdown and Logseq-style Markdown.
- Added support for page properties, block properties, `id:: UUID`, block refs `((uuid))`, and block embeds `{{embed ((uuid))}}`.
- Added a `SQLite + FTS5` metadata and lexical retrieval layer.
- Added a `LanceDB + bge-m3` vector retrieval path.
- Added preflight disk estimation and persisted preflight history.
- Added `scripts/run_gui.ps1` as the desktop development entry point.
- Added `omniclip_rag/formatting.py` for shared formatting logic.
- Added `README.zh-CN.md` as the secondary Chinese-facing documentation.
- Added bilingual desktop language switching, hover tooltips, and explicit newcomer-first quick-start guidance.
- Added a generated flat app icon for the desktop window, taskbar, and packaged EXE.

### Changed

- Switched the primary product surface from CLI-first to desktop GUI-first.
- Updated the EXE build to target a windowed desktop application.
- Extended cleanup support to include exported context packs.
- Tightened `.gitignore` to exclude local user data, build outputs, caches, and the local sample vault.
- Reworked public-facing documentation to an English-primary structure.
- Reworked the desktop layout into a clearer flat UI with a smaller header, lighter palette, more obvious first-run actions, and tabbed left-side workspace sections.

### Fixed

- Fixed vector storage not being cleared together with the SQLite index.
- Fixed false-positive local model readiness when cache directories were incomplete.
- Fixed Windows console / EXE output encoding issues.
- Fixed Tk font parsing issues that could block GUI startup on Windows.
- Fixed blurry desktop rendering by enabling Windows DPI awareness and updating the GUI font baseline.
- Fixed EXE packaging so icon resources are bundled and the executable embeds the product icon.

### Notes

- The current stable default remains `torch + bge-m3`.
- ONNX remains a future optimization path, not the default production route for `V0.1.0`.
- CLI remains available for debugging and automation, but GUI is now the primary workflow.
