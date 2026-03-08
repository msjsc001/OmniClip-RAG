
# Changelog

## V0.1.6 - 2026-03-08

### Added

- Added [RELEASE_NOTES_v0.1.6](releases/RELEASE_NOTES_v0.1.6.md) for the packaged-runtime bootstrap update.
- Added runtime bootstrap metadata so packaged builds can recover the external Python standard-library and DLL search paths needed by an installed `runtime/` folder.

### Changed

- Changed the Windows runtime installer to install `torch`, `sentence-transformers`, and the pinned support packages in a single pip resolution pass.
- Changed build packaging so local `dist/OmniClipRAG/runtime/` content is preserved across rebuilds instead of being deleted.
- Changed `.gitignore` so standalone `runtime/` folders stay untracked together with the existing build-output ignores.

### Fixed

- Fixed packaged runtime installation on Windows PowerShell by rewriting `InstallRuntime.ps1` as an ASCII-safe script.
- Fixed the CUDA runtime path so the installer no longer replaces a just-installed CUDA `torch` build with the CPU build during the second dependency step.
- Fixed packaged startup bootstrap so external runtime installs can be discovered before the app decides that `torch` or `sentence-transformers` are missing.

## V0.1.5 - 2026-03-08

### Added

- Synchronized the Chinese and English README files so both documents now describe the same workflow, product scope, data layout, and caution boundaries.

### Changed

- Improved the packaged-runtime guidance so CPU users are told to install the `cpu` profile instead of being nudged toward `disabled`.
- Reworked the runtime-missing dialog into a shorter action-oriented layout with direct commands, folder scope, and size estimates.

### Fixed

- Fixed the desktop task error path so `RuntimeDependencyError` now reaches the GUI as a friendly message instead of a traceback wall in the activity log.

## V0.1.4 - 2026-03-08

### Added

- Added [RELEASE_NOTES_v0.1.4](releases/RELEASE_NOTES_v0.1.4.md) for the runtime-messaging hotfix.

### Fixed

- Fixed the runtime-missing error path so packaged builds now raise a clear `RuntimeDependencyError` instead of crashing with `NameError: _runtime_dependency_message is not defined`.
- Fixed the device summary so a machine with NVIDIA + CUDA toolkit no longer looks “mysteriously broken”; the UI now tells the user that the lean app package still needs its own runtime install.

## V0.1.3 - 2026-03-08

### Added

- Added [RELEASE_NOTES_v0.1.3](releases/RELEASE_NOTES_v0.1.3.md) for the Windows packaging hotfix.

### Fixed

- Fixed the packaged Windows EXE by copying `pyarrow.libs` into the final onedir build, so `pyarrow` no longer fails during desktop startup.
- Fixed the published Windows release asset so the lightweight package stays small without shipping a broken `lancedb` / `pyarrow` startup path.

## V0.1.2 - 2026-03-08

### Added

- Added workspace-level build-history timing so later prechecks and rebuild ETAs can learn from real local runs.
- Added explicit cancel handling for full rebuilds alongside the existing pause / resume flow.
- Added the new Chinese product name **方寸引** across the desktop title and Chinese-facing documentation.
- Added [RELEASE_NOTES_v0.1.2](releases/RELEASE_NOTES_v0.1.2.md) for the runtime and naming update.
- Added [RUNTIME_SETUP.md](RUNTIME_SETUP.md) and a packaged `InstallRuntime.ps1` flow for lean Windows releases.

### Changed

- Changed the default device policy from fixed `cpu` to `auto`, so the app can use CUDA automatically when the active runtime truly supports it.
- Changed the Chinese-facing product name from `无界 RAG` to `方寸引`.
- Changed the precheck and rebuild ETA pipeline to blend static expectations, live progress, and recent workspace history.
- Changed the public README files so they match the current product positioning, acceleration behavior, data-layout model, and lean-release packaging strategy.

### Fixed

- Fixed the vector rebuild path so pause / resume and live ETA use the real interruptible batching implementation instead of an older coarse-grained loop.
- Fixed Windows release bloat by removing very large optional AI runtimes from the main packaged app and moving them to a separate runtime-install flow.
- Fixed clipboard export failures on Chinese Windows by bypassing `gbk` console encoding when writing context packs to `clip`.
- Fixed paused rebuild timing so elapsed time no longer keeps drifting while work is suspended.
- Fixed the last remaining test and UI naming references that still pointed at the old Chinese product name.

## V0.1.1 - 2026-03-08

### Added

- Added multi-vault switching with isolated per-vault workspaces.
- Added explicit separation between shared cross-vault data and vault-specific workspace data.
- Added auto-download / manual-download model selection with `hf-mirror.com` and Hugging Face fallback guidance.
- Added space-and-time prechecks that estimate both disk usage and first-run duration.
- Added persisted unfinished-build state, restart-time resume prompts, and real pause/resume for full rebuilds.
- Added live task progress, elapsed-time, and ETA feedback to the desktop task panel.
- Added workspace-level build-history timing so later prechecks and rebuild ETAs can learn from real local runs.
- Added regression tests for paused rebuild control in the service layer, vector layer, and GUI layer.
- Added cancel support for full rebuilds so users can stop an in-flight rebuild without leaving an unhandled task failure.
- Added score-threshold filtering and per-hit include toggles so users can curate the final context pack before copying.
- Added runtime device capability reporting for `auto` / `cpu` / `cuda` selection in the desktop settings panel.
- Added [RELEASE_NOTES_v0.1.1](releases/RELEASE_NOTES_v0.1.1.md) for the post-`v0.1.0` desktop hardening update.

### Changed

- Reworked the desktop app into a more newcomer-first bilingual workflow with clearer guidance and fuller runtime localization.
- Changed data layout so model cache and general logs live under `shared/`, while each vault keeps its own isolated workspace under `workspaces/<workspace-id>/`.
- Changed the preflight flow to explain both storage cost and estimated build/download time before heavy operations begin.
- Changed model setup prompts so users can choose automatic download or manual download instructions before the app starts pulling files.
- Changed model loading so a valid local model cache is treated as authoritative and is reused across query, rebuild, and bootstrap flows.
- Changed full rebuild execution to support interruption recovery, batch-based vector writes, and explicit desktop-side pause/resume control.
- Changed desktop task feedback so users see task state, elapsed time, ETA, and rebuild progress instead of waiting silently.
- Changed the default device policy from fixed `cpu` to `auto`, so the runtime can use CUDA when it is genuinely available and otherwise fall back safely.
- Changed the quick-start area into a collapsible newcomer guide so the first screen stays compact while preserving onboarding help.
- Changed query presentation to show explicit hit reasons, focused excerpts, and a 0-100 relevance scale that maps to the visible score filter.
- Changed build packaging hygiene so only the formal `dist/OmniClipRAG/` delivery folder is kept after validation.

### Fixed

- Fixed unnecessary Hugging Face network calls during search or indexing when a complete local model cache already exists.
- Fixed rebuild failures caused by unreadable Markdown files by skipping unreadable files instead of aborting the whole run.
- Fixed rebuild crashes caused by duplicated Logseq `id:: UUID` values by demoting later duplicates instead of breaking SQLite insertion.
- Fixed stale or invalid split-pane state that could collapse parts of the desktop layout after launch.
- Fixed first-run friction where startup background work could collide with a user's first button click.
- Fixed repeated model-download prompting when the model was already present and passed local integrity checks.
- Fixed rebuild progress plumbing so the vector stage actually uses the interruptible batch path that pause / resume and live ETA depend on.
- Fixed clipboard export failures on Chinese Windows by bypassing `gbk` console encoding when writing context packs to `clip`.
- Fixed paused rebuild timing so elapsed time and ETA no longer keep drifting while work is suspended.
- Fixed query-result usability by preventing the context pack from blindly copying every returned hit unless the user keeps it selected.

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
