# Changelog

## Unreleased

## V0.3.0 - 2026-03-16

### Added

- Added the isolated extension-format subsystem under `omniclip_rag/extensions/`, including dedicated PDF parsing/indexing, Tika runtime management, white-listed Tika format selection, independent watch state, and cross-source query broker support.
- Added a componentized Runtime management flow with a dedicated Qt page, per-component repair/cleanup, pending-update staging, and official-vs-mirror manual/automatic install guidance.
- Added a packaged self-check query path plus the Markdown query/runtime RCA planning and trace infrastructure, so query diagnosis can be resumed from repo docs instead of conversation history.
- Added [RELEASE_NOTES_v0.3.0](releases/RELEASE_NOTES_v0.3.0.md) for this source-only extension/runtime milestone release.

### Changed

- Changed the desktop app to a Qt-only shell by removing the legacy Tk UI path from the repository and keeping future fixes focused on one real desktop implementation.
- Changed the packaged launcher/runtime bootstrap chain to handle pending runtime updates, startup/runtime diagnostics, and packaged-vs-development behavior more explicitly.
- Changed Runtime UX from one opaque repair block into component-oriented management that separates semantic core, vector storage support, and optional NVIDIA/CUDA acceleration.
- Changed Markdown query diagnostics so capability logs, execution-path traces, and packaged self-check evidence can be compared against real GUI behavior during RCA.

### Fixed

- Fixed multiple runtime repair drift issues around pending-vs-live application, script-version mismatch, PowerShell compatibility, and stale health checks that previously made successful repairs still look broken.
- Fixed packaged startup regressions related to launcher bootstrap, Qt runtime loading, and startup sequencing so the EXE can surface earlier, safer startup behavior.
- Fixed several GUI/query synchronization issues around live config snapshots, runtime status surfacing, and source-filter/query-path plumbing while the Markdown main-query RCA remains in progress.

## V0.2.4 - 2026-03-14

### Added

- Added a dedicated leftmost result-index column in the Qt query results table so each hit now shows its visible sequence number during review.
- Added [RELEASE_NOTES_v0.2.4](releases/RELEASE_NOTES_v0.2.4.md) for the query-workspace readability release.

### Changed

- Changed the Qt query toolbar to remove `Search and copy` plus `Copy current context`, leaving `Search` as the single primary action in the top input row.
- Changed result-table column sizing so the new sequence column fits without collapsing the main page, reason, anchor, and score columns too aggressively.
- Changed the idle query-status copy so it now points only at the remaining visible top-row action.

### Fixed

- Fixed the lack of row-level counting in `Results & Details`, which previously made it harder to tell which hit you were currently reading in a longer result list.

## V0.2.3 - 2026-03-13

### Added

- Added a dedicated copyable manual-download dialog for missing `BAAI/bge-m3`, including the target folder, Hugging Face CLI bootstrap command, official source link, mirror link, and ready-to-run Windows terminal commands.
- Added a shared `model_download_guidance_context()` helper so Qt, Tk, and future entry points reuse the same AppData-aware model-path and manual-command generation logic.
- Added [RELEASE_NOTES_v0.2.3](releases/RELEASE_NOTES_v0.2.3.md) for the model-download usability and documentation refresh release.

### Changed

- Changed the model-missing flow so clicking `Download BAAI/bge-m3 model` now explicitly splits into automatic download versus manual download guidance instead of falling back to a weak plain-text hint.
- Changed the legacy Tk manual-download path to copy the full instruction block to the clipboard before showing the dialog, compensating for old message-box text-selection limits.
- Changed README positioning and captured the future extension-format isolation plan in repo docs so the next subsystem can be resumed without losing architectural intent.

### Fixed

- Fixed missing-model guidance so manual users now get executable commands with the correct target folder created in advance, instead of only getting generic web links.

## V0.2.2 - 2026-03-13

### Added

- Added compact rebuild-state persistence that stores durable cursors and manifest signatures instead of giant per-file path payloads.
- Added large-vault rebuild watchdog diagnostics that emit structured reports when forward progress stalls for too long.
- Added safe-startup recovery tracking so the next launch can clear process-level vector resources after RAM/VRAM incidents or dirty exits.
- Added [RELEASE_NOTES_v0.2.2](releases/RELEASE_NOTES_v0.2.2.md) for the large-vault durability and versioned-release build update.

### Changed

- Changed the visible app/release version to `v0.2.2` across the packaged UI, Python package metadata, and release documentation.
- Changed rendering and query hydration to prefer lazy block/chunk lookup paths instead of eagerly materializing full lookup maps from SQLite.
- Changed packaged Windows builds to land in versioned folders such as `dist/OmniClipRAG-v0.2.2/`, preserving any existing `runtime/` inside that version folder and leaving older version folders untouched.
- Changed the packaged executable name from `launcher.exe` to `OmniClipRAG.exe`, aligning the EXE, release asset, runtime instructions, and Windows process name with the product name.

### Fixed

- Fixed rebuild-resume durability so vector continuation now trims a small suffix before replay, reducing dirty tails after crashes, power loss, or forced termination.
- Fixed large-vault render refreshes that previously needed whole-database block lookup tables in memory before rebuilding visible chunks.
- Fixed post-memory-pressure packaged startup so a dirty runtime incident is less likely to poison the next launch until the machine is rebooted.

## V0.2.1 - 2026-03-12

### Added

- Added rolling file logging with configurable size limits plus in-app open/clear controls under the data settings flow.
- Added clearer device/runtime status surfacing in the Configure page, including GPU presence, CUDA readiness, runtime completeness, CPU fallback availability, and the current effective mode.
- Added vector-stage recovery and backpressure messaging so large packaged rebuilds can explain when they are flushing, shrinking, or waiting under resource pressure.
- Added [RELEASE_NOTES_v0.2.1](releases/RELEASE_NOTES_v0.2.1.md) for the post-Qt stabilization, runtime-clarity, and large-vault hardening release.

### Changed

- Changed the visible app/release version to `v0.2.1` across the packaged UI, Python package metadata, and release documentation.
- Changed model bootstrap behavior so it focuses on the selected embedding model itself instead of incorrectly piggybacking on CUDA/runtime guidance.
- Changed rebuild progress presentation to keep one truthful overall percentage while vector details separately report encoded, written, flushing, and recovery states.
- Changed resource-pressure handling during vector rebuilds so the pipeline proactively shrinks and yields under RAM/VRAM contention instead of waiting for late catastrophic pressure.

### Fixed

- Fixed the reranker toggle contract so disabling the optional reranker actually prevents reranker execution in the service layer.
- Fixed late runtime-guidance surprises by moving vector-runtime preflight checks earlier for rebuild, watch, query, and warmup entry points.
- Fixed writer-side vector tail handling so memory-pressure retries use smaller write batches without dropping staged rows before a successful write.
- Fixed packaged large-vault rebuild visibility so recovery periods no longer look like silent hangs when rebuild progress temporarily stalls under heavy resource pressure.

## V0.2.0 - 2026-03-11

### Added

- Added a rebuilt Qt desktop shell that now owns the packaged app flow, with a persistent `简体中文 / English` language selector in the top bar and full-window language switching.
- Added a structured CUDA/runtime guidance dialog with copyable status, install steps, and runtime completeness checks instead of relying on a plain message box.
- Added an independent hot-watch hardware-peak control plus a clickable preflight-success shortcut that jumps straight to the query activity log.
- Added [RELEASE_NOTES_v0.2.0](releases/RELEASE_NOTES_v0.2.0.md) for the Qt rewrite, state-hardening, and lean-release milestone.

### Changed

- Changed the desktop workflow so `Query` and `Configure` now form one cohesive Qt shell with persisted theme/scale preferences, refined settings copy, and clearer runtime guidance.
- Changed rebuild/watch progress feedback to use finer-grained backend updates, truthful remaining-time language, and clearer vector-tail progress reporting.
- Changed the Windows packaging flow back to the historical lean layout under `dist/OmniClipRAG/`, while still preserving any existing local `dist/OmniClipRAG/runtime/` folder across rebuilds.
- Changed the CUDA device label shown in UI copy to `CUDA(N卡GPU)` while keeping the persisted internal config value stable as `cuda`.

### Fixed

- Fixed index-state pollution so missing or cancelled rebuilds no longer reappear after restart as if a usable index already existed.
- Fixed hot-watch enable/disable behavior so watch start is blocked honestly when the index is missing or pending, stop signals close the state loop correctly, and blocked starts surface explicit feedback.
- Fixed large-vault rebuild crashes at the final vector-write tail by bounding late LanceDB write batches and releasing RAM/VRAM pressure before the final drain.
- Fixed the Qt preflight-success jump, runtime-status refresh chain, and immediate bilingual text refresh so the visible shell now stays aligned with the real backend/app state.


## V0.1.11 - 2026-03-11

### Added

- Added a page-level `Page Sort` toggle in the results table so users can regroup hits by note and order notes by average fragment score, with one-click restore back to fragment order.
- Added a dedicated query-status banner on the query page that clearly shows idle, blocked, running, and completed states with stage-aware progress feedback.
- Added a `UI` settings page with persisted text scaling plus `Light / Dark / Follow system` theme controls.

### Changed

- Changed quick-start and advanced-option toggles to local show/hide behavior instead of rebuilding the whole desktop window for small visibility changes.
- Changed high-frequency `Configure`-driven layout work so wrap recalculation, scroll-region sync, and canvas width updates are now coalesced through deferred UI callbacks during pane dragging.
- Changed text panels, sensitive-filter editing, and query-status surfaces to follow the active theme and scaled UI typography more consistently.
- Changed the packaged Windows release flow so the local `dist/OmniClipRAG/runtime/` folder remains preserved across rebuilds, while the public release zip stays runtime-free.

### Fixed

- Fixed the most obvious GUI lag on the `Start` page and `Query` page by removing full-root rerenders from routine UI interaction paths.
- Fixed duplicate root layout tracking from piling up across rerenders, which could amplify redraw work over time.
- Fixed query-task feedback so the visible query state now reflects real backend query progress instead of leaving users unsure whether search is still running.

## V0.1.10 - 2026-03-10

### Added

- Added a dedicated `Retrieval Boost` settings area so reranker controls, AI-collaboration export, CPU / GPU batch sizing, and reranker readiness are easier to understand and adjust.
- Added explicit reranker bootstrap choices for automatic download versus manual placement, including exact cache-folder guidance and ready-state detection.
- Added compact same-parent sibling merging in full-context export so AI-facing context packs keep local structure while reducing repeated branches.

### Changed

- Changed LanceDB full rebuilds to use a bounded single-writer pipeline so encoding and vector writes can overlap without introducing aggressive multi-writer instability.
- Changed build tuning so write-queue backlog can grow write batches and slightly cool encode batches when late-stage write pressure starts dominating.
- Changed vector rebuilds so encoded vectors are materialized into rows inside the writer stage, allowing encode, row preparation, and LanceDB writes to overlap more cleanly without aggressive multi-writer risk.
- Changed the minimum relevance guidance and defaults to recommend starting around `20`, which better matches the current hybrid-score calibration.
- Changed full-context export so same-parent sibling hits can be merged into one compact fragment group, reducing repeated local branches while keeping result tables atomic.
- Changed final minimum-relevance filtering so it applies to the final displayed score after reranking, preventing low-score rows from leaking into the visible result table.

### Fixed

- Fixed reranker bootstrap so download/manual staging works even before reranking is enabled, and added explicit manual-download guidance plus duplicate-download protection.
- Fixed the `CrossEncoder(... local_files_only=...)` bootstrap error caused by passing `local_files_only` through two different argument paths.
- Fixed Windows helper subprocesses used during rebuild/runtime probing so `nvidia-smi`, `nvcc`, and clipboard commands run without flashing black console windows or stealing focus.

## V0.1.9 - 2026-03-10

### Added

- Added backend-only retrieval-shaping modules for query profiling, runtime hit selection, optional reranking, and query-limit recommendation history.
- Added optional `BAAI/bge-reranker-v2-m3` support with manual bootstrap, batching, truncation, CUDA OOM recovery, CPU fallback, and GUI settings.
- Added an optional AI collaboration export mode that appends minimal retrieval guidance without forcing a fixed tail prompt into every context pack.
- Added a build-performance controller with `30% / 50% / 90%` hardware-peak profiles, Windows CPU/GPU/memory sampling, adaptive encode/write batch sizing, and live rebuild tuning summaries in the desktop task panel.
- Added a phase-aware rebuild ETA tracker that blends static history, current-stage recent throughput, and vector tail-rate history instead of trusting one linear average.

### Changed

- Changed large-vault rendering and vector rebuild paths to stream in bounded batches instead of materializing the whole rendered/vector document set at once.
- Changed storage-layer `IN (...)` reads/deletes to honor SQLite variable limits dynamically, so huge vaults and huge single pages no longer fail near the end of indexing.
- Changed mixed retrieval to run through typed query profiles so short terms, concept terms, and natural-language queries can use different fusion and candidate-pool strategies.
- Changed same-page post-selection to novelty-based suppression instead of blunt same-page penalties, reducing duplicate evidence without discarding complementary fragments.
- Changed query-limit guidance so it is derived from persisted runtime history and reranker state, while remaining advisory rather than auto-rewriting user settings.
- Changed vector rebuild batching so CUDA encode size and LanceDB write size are tuned independently, with OOM-triggered shrink behavior and resource-aware expansion under headroom.
- Changed rebuild ETA updates so indexing, rendering, and vectorizing now use their own recent windows, and vector ETA can learn from tail-speed history written by previous runs.

### Fixed

- Fixed `sqlite3.OperationalError: too many SQL variables` during late-stage render/FTS refresh on large vaults.
- Fixed large single-page incremental updates so tens of thousands of chunks no longer build oversized vector delete/upsert batches.
- Fixed the query-limit tooltip/runtime contract so GUI hints no longer depend on local recomputation or `__dict__` access against slots dataclasses.
- Fixed reranker local-model readiness checks so a locally present cross-encoder can be used without being mistaken for a missing embedding cache.
- Fixed AI collaboration export language fallback so Chinese retrieval sessions stay Chinese even on an English system locale.

## V0.1.8 - 2026-03-09

### Added

- Added [RELEASE_NOTES_v0.1.8](releases/RELEASE_NOTES_v0.1.8.md) for the live-watch hardening release.
- Added persisted `watch_state.json` tracking for offline guard state, dirty render paths, dirty vector paths, and dirty vector chunk ids.
- Added desktop activity-log messages for vault offline, vault recovery, watch repair, and watch retry events.
- Added regression coverage for parse-first reindexing, vector-dirty repair, and offline snapshot handling.

### Changed

- Changed incremental watch refresh to parse changed files before swapping index rows, so temporary read/parse failures keep the previous index intact.
- Changed watch mode so watchdog and polling both run through the same `snapshot diff + stability window + delete confirmation` flow instead of trusting raw file events directly.
- Changed watch recovery so SQLite stays authoritative while vector writes can lag behind and repair later.
- Changed state-file writes to atomic replace with Windows-safe temporary files and retry logic.

### Fixed

- Fixed the risk of temporarily locked or half-written Markdown files wiping still-valid indexed content during hot updates.
- Fixed vault drop/offline scenarios so an encrypted drive disappearing is no longer interpreted as mass deletion.
- Fixed watch crash-recovery gaps by replaying dirty render/vector work from persisted workspace state.

## V0.1.7 - 2026-03-09

### Added

- Added [RELEASE_NOTES_v0.1.7](releases/RELEASE_NOTES_v0.1.7.md) for the chunk-first retrieval and desktop workflow refresh.
- Added source-faithful context export that groups results by note title and emits numbered Markdown snippets instead of whole-page dumps.
- Added configurable page-title regex filtering, sensitive-content redaction controls, per-panel text search, and page-jump statistics for full-context review.
- Added rebuild confirmation when an existing index is already present.

### Changed

- Changed hybrid retrieval so lexical candidates and vector-only candidates are merged before final scoring instead of letting lexical rows suppress semantic-only recall.
- Changed the visible relevance score into a better-calibrated `0-100` engineering score that reflects lexical hits, FTS, LIKE, vector similarity, and anti-noise penalties.
- Changed the desktop shell to a two-tab `Query / Config` layout with sortable result columns, stronger layout persistence, and tighter header/config spacing.
- Changed single-character query handling so one-character searches stay lexical-only by default.
- Changed full-context export labels from `笔记片段A/B/...` to numbered fragments.

### Fixed

- Fixed whole-page fallback for list-heavy Markdown files so outline notes are exported as focused evidence blocks instead of noisy page dumps.
- Fixed Logseq block refs and embeds so UUIDs are resolved back to readable text in previews and exported context.
- Fixed exported context leakage risk by masking high-risk secrets and keeping user data out of the repository/program directory.
- Fixed responsive helper-text wrapping so the top green guidance text no longer oscillates while the window resizes.

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



