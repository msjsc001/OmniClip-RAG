# Changelog

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
- Reworked the desktop layout into a clearer flat UI with a smaller header, lighter palette, and more obvious first-run actions.
- Changed model-missing behavior so the GUI now prompts the user to download and warm the model instead of leaving the first search or rebuild unexplained.

### Fixed

- Fixed vector storage not being cleared together with the SQLite index.
- Fixed false-positive local model readiness when cache directories were incomplete.
- Fixed Windows console / EXE output encoding issues.
- Fixed Tk font parsing issues that could block GUI startup on Windows.
- Fixed blurry desktop rendering by enabling Windows DPI awareness and updating the GUI font baseline.
- Fixed EXE packaging so icon resources are bundled and the executable embeds the product icon.
- Fixed startup failure when the default `%APPDATA%` path was not writable.
- Fixed EXE packaging flow to remove meaningless `.spec` leftovers after build.

### Notes

- The current stable default remains `torch + bge-m3`.
- ONNX remains a future optimization path, not the default production route for `V0.1.0`.
- CLI remains available for debugging and automation, but GUI is now the primary workflow.
