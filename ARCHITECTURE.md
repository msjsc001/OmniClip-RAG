# Architecture Notes

## Release Boundary For Current Mainline

The current mainline is not trying to ship a giant all-in-one AI platform.

Its delivery goal is narrower and much more deliberate:

1. parse local Markdown / Logseq vaults reliably,
2. index and update them reliably,
3. expose a clean desktop workflow,
4. keep the final bridge to AI tools loosely coupled.

## Current System Shape

The project already includes:

- a Logseq / standard Markdown dual parser,
- a SQLite metadata authority,
- `FTS5 + LIKE + structure scoring` candidate retrieval,
- `LanceDB + bge-m3` vector retrieval,
- an isolated extension-format subsystem for PDF and Tika-backed formats,
- a componentized Runtime sidecar manager for packaged builds,
- a shared headless bootstrap for non-GUI shells,
- a read-only MCP server line over the same retrieval core,
- a desktop GUI workflow,
- a CLI workflow for debugging,
- local model bootstrap,
- local-only querying,
- data directory management and selective cleanup,
- a Windows EXE build chain,
- project-local dependency isolation via `.packages`.

## Key Decisions

### 1. SQLite is the source of truth; LanceDB is a retrieval layer

SQLite stores:

- file registry,
- page metadata,
- block / section metadata,
- reference edges,
- FTS index,
- preflight history.

LanceDB is only responsible for vector retrieval.

Why: hot reload, delete handling, dependency invalidation, and debugging all become tractable when the system has one clear authority.

### 2. Retrieval must be hybrid

The current query path is:

- `SQLite FTS5 + LIKE` to gather candidates,
- structure-aware scoring across title / anchor / rendered text,
- vector score fusion from LanceDB,
- final context-pack assembly.

Why: pure vector search misses exact concepts and private terminology; pure lexical search misses semantic neighbors.

### 3. Incremental refresh follows dependency closure, not just touched files

When a file changes, the system refreshes:

- that file,
- blocks that directly reference it,
- and upstream blocks affected by nested references or embeds.

Why: `((uuid))` and `{{embed ((uuid))}}` change the final rendered meaning of dependent chunks.

### 4. GUI and backend stay cleanly separated

Current layering:

- `gui.py`: window, controls, background task orchestration,
- `service.py`: indexing, querying, watching, cleanup, status,
- `storage.py`: SQLite schema, FTS, refs, history,
- `vector_index.py`: model bootstrap, embedding, LanceDB,
- `parser.py`: syntax parsing and chunk extraction.

Why: if GUI logic leaks into retrieval/storage internals, later additions such as tray mode, hotkeys, and advanced settings become expensive and brittle.

The new `Retrieval Boost` tab follows the same boundary: GUI only exposes reranker/model/export controls and status, while query shaping, reranker readiness, and export behavior stay in backend modules.

Reranker bootstrap also stays independent from the enable checkbox: users may download or manually stage the reranker cache before ever turning reranking on for actual queries.

Full-context export now deliberately diverges from raw hit listing: result tables stay atomic for inspection, but the exported context pack may merge same-parent sibling fragments into one compact local structure so AI-facing output keeps evidence density without repeating nearly identical branches.


### 5. Watch mode must be externally stoppable

CLI-only blocking watch was not enough for a desktop app.

The GUI-required watch contract now includes:

- start,
- stop,
- background execution,
- update callbacks,
- visible status feedback.

Why: a desktop app must let the user manage background listeners safely and explicitly.

### 6. User data must stay out of the program directory

The app prefers `%APPDATA%\OmniClip RAG` and only falls back to `%LOCALAPPDATA%\OmniClip RAG` when needed. It must not write user data, indexes, logs, or exports into the program directory or repository working tree.

Why: the storage convention is correct, and it also prevents personal data, test indexes, or exported context packs from leaking into source-controlled paths.

### 7. Hugging Face state is fully localized

Current model/cache policy:

- `HF_HOME` is redirected into the app-owned cache area,
- vector model / reranker download, delete, log, and validation targets always follow the current active `data_root`,
- changing `data_root` itself still requires `Save + restart`; the product must not start writing model files into a preview-only root before that switch is active,
- model files are downloaded into explicit local model directories,
- automatic and manual model download flows share the same exclude list for non-essential assets such as `imgs/.DS_Store`,
- vector model and reranker automatic downloads run in a dedicated headless worker subprocess of the same EXE/Python entrypoint, not inside the main GUI thread,
- the separate Windows terminal must show the real worker stdout for live download progress and faults, while the GUI follows the same per-download log file for status updates instead of reusing the generic task progress card,
- the recommended source chain for `BAAI/bge-m3` and `BAAI/bge-reranker-v2-m3` is now `ModelScope -> HF mirror -> Hugging Face official`, so China-mainland users are not forced to start from the public HF endpoint,
- the download worker emits an immediate heartbeat and then a fixed-interval heartbeat with byte/file deltas for both the target model directory and repo cache, so users can tell the difference between "still waiting for remote response" and "bytes are actively growing",
- the frozen EXE download worker must synthesize writable `stdout/stderr` streams before entering third-party download stacks such as ModelScope/tqdm, otherwise progress-bar initialization can crash with `'NoneType' object has no attribute 'write'`,
- GUI stall supervision must treat heartbeat-only log lines as visibility signals, not as real download progress; automatic source switching is based on substantive log events or byte growth,
- mirror-source automatic downloads are supervised by the GUI; if the recommended path shows no substantive progress and no byte growth for a sustained window, the app kills that worker and retries the next fallback source once,
- mirror-source model downloads may retry once against the official Hugging Face endpoint when the mirror rejects repo-specific junk files,
- reranker bootstrap from the settings UI is download-only; model warmup stays lazy and happens later on actual query/runtime use,
- deleting a local model or reranker must remove both the visible model directory and that repo's `_hf_home/hub` cache subtree after releasing in-process handles,
- Xet transport is disabled,
- default user cache assumptions are avoided,
- local-only mode is supported for bootstrap/query/index flows.

Why: the default Windows cache path is too easy to corrupt with permission issues, symlink edge cases, and partial downloads.
Mirrors are also less tolerant of repository-side junk files than the official endpoint, so the product must keep all four download paths aligned instead of letting `auto/manual` or `mirror/official` silently diverge.

### 8. Unreadable Markdown files must be skippable, not fatal

Current fault-tolerance rule:

- preflight skips unreadable `.md` files and records that fact in the estimate notes,
- rebuild/reindex skips unreadable `.md` files instead of aborting the whole run,
- if every discovered Markdown file is unreadable, preflight blocks the workflow and tells the user the chosen folder is not a real vault root.

Why: users can accidentally point the app at a home directory, browser profile, or synced workspace that contains Markdown files they do not actually own or cannot read. One bad file must not collapse the whole product.

### 9. Tika extension indexing is compatibility-first, not XHTML-first

Current Tika build/query policy:

- the Tika sidecar is allowed to return different surfaces for different formats,
- extension indexing now prefers `text/plain` extraction first,
- if plain text is unavailable or rejected, the system falls back to `rmeta` JSON and extracts body text from metadata,
- raw XHTML is no longer the default success contract for Tika-backed formats,
- the success criterion is "extractable body text that can be normalized into chunks", not "an XHTML response exists",
- empty files, unreadable files, or files with no extracted body text are treated as expected skips rather than product-level failures,
- transport failures, unsupported responses, or exhausted fallback chains are tracked as true parse failures and surfaced back to the UI.

Why: real Tika 3.x behavior is format-dependent. Binding the whole extension pipeline to `PUT /tika + Accept: application/xhtml+xml` caused valid EPUB/DOCX files to fail with `HTTP 406`, which looked like "all files were skipped" even though the sidecar was healthy. A text-first contract makes newly exposed Tika formats far more likely to index without format-specific branching.

### 10. MCP is a standard interface layer, not a second backend

Current MCP policy:

- GUI and MCP share one bootstrap path for Runtime discovery, DataPaths, and QueryService creation,
- the GUI remains a `windowed` shell,
- MCP is delivered as a separate `console/headless` shell,
- MCP V1 is `tools-only`,
- MCP V1 is strictly read-only,
- MCP search returns both machine-friendly structured output and model-friendly readable text,
- MCP may degrade to lexical-only, but must say so explicitly.

Why: trying to bolt stdio MCP onto the existing windowed GUI executable would make transport behavior fragile on Windows and would tempt the codebase into a split-brain state where GUI and MCP silently diverge. The durable design is dual-shell / same-core.

### 11. MCP discovery and distribution now target the official Registry, not the deprecated servers README

Current MCP publishing policy:

- third-party discoverability is now aligned to the official MCP Registry rather than the old `modelcontextprotocol/servers` README list,
- the MCP release line now has a dedicated Registry metadata file (`server.json`),
- the Registry-facing artifact is a Windows MCPB binary bundle, not only a raw ZIP,
- ZIP delivery is still preserved for manual local users,
- the first Registry-facing version is intentionally `v0.4.1`,
- the first Registry publish is intentionally manual instead of CI-driven.

Why: the official ecosystem has already moved third-party discovery to the Registry, and Registry metadata becomes effectively immutable once published. That makes packaging discipline, SHA tracking, and version hygiene more important than chasing a legacy README mention.

### 12. The project website is a static trust layer, not a second frontend product

Current website policy:

- the public project site lives in the repository `docs/` directory,
- it is published through GitHub Pages from `main /docs`,
- the published `docs/` tree includes a committed `.nojekyll` marker so Pages serves the site as plain static output without Jekyll rewriting or underscore-path surprises,
- it uses plain `HTML + CSS + a very small amount of native JS`,
- it does not introduce React, Vue, Tailwind, npm build chains, or third-party UI runtime dependencies,
- all page assets are referenced through relative paths so the site remains correct under the GitHub Pages repository subpath,
- social preview metadata uses absolute HTTPS image URLs because external crawlers cannot resolve repository-relative asset paths,
- the landing page defaults to English, provides an explicit Chinese toggle, and persists the chosen language locally,
- the site is intentionally a hybrid of project website and product landing page,
- the site prioritizes private/local-first retrieval as the first identity and treats MCP as a strong secondary delivery line,
- the site must always pair a conceptual hero visual with real product screenshots so style never outruns trust.

Why: this repository needs a durable public-facing trust surface for GitHub, Registry, and community traffic, but it does not need a second app-shaped frontend subsystem with its own toolchain, dependency drift, and maintenance tax.

### 13. The website now follows a single-exhibit visual grammar

Current website composition policy:

- the public site is no longer treated as a stack of equally-designed product sections,
- the page is organized as one exhibit built around a single mother theme,
- the theme is expressed as `core / orbit / fragment / boundary`,
- the page only uses three visual grammars:
  - `Canvas` for manifesto and stance,
  - `Sheet` for steps and evidence,
  - `Plate` for abstract diagrams and seals,
- Chinese remains the cover-language anchor,
- English is allowed to condense and transcreate instead of tracking Chinese sentence length literally,
- the `Screens` section is the single evidence climax rather than three equal product cards,
- abstract illustrations are staged through a shared `art-stage` layer instead of being solved only through blend-mode tricks.

Why: the website had already passed the “competent project landing page” stage, but it still read as multiple polished sections rather than one coherent work. Compressing the page into a smaller set of visual species is the highest-leverage way to raise artistic quality without abandoning the static low-cost stack.

### 14. Website polishing now follows deletion-first rules, not completion-first rules

Current finishing policy:

- the website should no longer chase “every section feels equally complete,”
- Hero remains the only manifesto-scale black anchor,
- Screens remains the only evidence climax and must preserve a clear primary / secondary / tertiary hierarchy,
- Trust is treated as a seal / inscription rather than a final feature list,
- Summary is treated as a wall of laws rather than a summary card row,
- Workflow is treated as note sheets rather than polished feature cards,
- explanatory copy should be shortened whenever it exists mainly to reassure the author instead of strengthening the work.

Why: once the site enters exhibit territory, the biggest risk is not lack of polish but softness. The most valuable late-stage changes are usually subtractive: fewer containers, fewer sentences, fewer equal-weight peaks, and a clearer visual chain of command.

### 15. The website now treats peak hierarchy as more important than completeness

Current peak-order policy:

- Hero is the manifesto peak and must remain the single darkest anchor on the page,
- Screens is the evidence peak and must give the first control plate clear central authority over the later supporting plates,
- Trust is the sealing peak and should read as a final inscription rather than a final feature section,
- Summary must feel like three laws pinned beneath the manifesto rather than a helpful summary row,
- Workflow must feel lighter and colder than Screens so the page keeps one evidence climax,
- late-stage edits should prefer deleting explanatory copy over improving section completeness.

Why: the page is no longer trying to behave like a balanced product site. Its quality ceiling now depends on whether the three main peaks dominate the reading order and whether everything else is willing to retreat.

### 16. Final website polish now optimizes for authority gaps, not local prettiness

Current late-stage polishing policy:

- the first control plate in `Screens` is allowed to be slightly unfair if that strengthens its role as the central evidence object,
- later control plates must retreat not only in size but also in caption formality, spacing, and plate-label stability,
- `Workflow` may be cooled further, but only if it still reads as a sequence of cold nodes rather than weakened UI scraps,
- `Core Logic` and `MCP` should get harder by deleting explanatory shell text while preserving the judgment-bearing skeleton.

Why: once the site is already coherent, the remaining gains no longer come from prettier local components. They come from widening authority gaps between peaks and retreating anything that softens those power relationships.

### 17. `data_root` is now the single environment truth

Current data-root policy:

- the active OmniClip environment is resolved from one startup truth chain only:
  1. explicit override,
  2. explicit test/developer override,
  3. `%APPDATA%\OmniClip RAG\bootstrap.json`,
  4. first-run default `%APPDATA%\OmniClip RAG-default`,
- the bootstrap file is only a locator and may remember multiple known roots, but it does not store user data,
- the real environment lives entirely under the selected `data_root`,
- config, workspaces, logs, cache, models, main Runtime, and Tika Runtime all derive from that same root,
- Runtime install / repair commands must explicitly target `<active data_root>/shared/runtime` instead of relying on installer-side default-root fallback,
- model bootstrap and manual model staging must always point to the cache tree under the current active `data_root`,
- the legacy `%APPDATA%\OmniClip RAG` environment remains switchable and recognizable,
- switching data roots means switching environments, not migrating data.

Why: the product semantic is no longer "pick a save folder". It is "pick the current world". Allowing GUI, MCP, EXE, Runtime, or logging to guess different roots creates split-brain state and silent data leakage.

### 18. Startup root resolution must be read-only and log-safe

Current startup contract:

- resolving the active data root is a pure read/probe phase,
- startup root resolution must not create directories, migrate files, or write bootstrap state,
- persistent file logging must not initialize before the active data root has been resolved and validated,
- if the active data root is unavailable, GUI enters a blocking chooser flow and headless/MCP fail explicitly,
- there is no product-level silent fallback from `%APPDATA%` to `%LOCALAPPDATA%` or `%TEMP%`.

Why: mixing "figure out truth" with "touch disk" caused exactly the class of failures this refactor is meant to eliminate: polluted bootstrap state, stray logs under default roots, and runtime/layout decisions drifting away from the user-selected environment.

## Module Boundary

- `omniclip_rag.config`: configuration and data paths
- `omniclip_rag.parser`: vault parsing
- `omniclip_rag.storage`: SQLite, FTS, refs, preflight history
- `omniclip_rag.preflight`: disk estimation
- `omniclip_rag.vector_index`: embeddings and LanceDB
- `omniclip_rag.service`: indexing, querying, watching, cleanup
- `omniclip_rag.headless`: shared non-GUI bootstrap and Runtime/DataPath wiring
- `omniclip_rag.mcp`: read-only MCP tool layer and protocol adapter
- `omniclip_rag.extensions`: isolated extension-format runtimes, registries, parsers, and build/query services
- `omniclip_rag.gui`: desktop interaction layer
- `omniclip_rag.clipboard`: clipboard handoff

## Verified So Far

Current validation includes:

- sample vault indexing with `3` files, `30` chunks, and `10` refs,
- successful `bge-m3` warmup with `1024`-dimensional embeddings,
- successful bootstrap / index / query / watch flows,
- successful GUI startup and shutdown,
- successful Runtime shared-root and legacy-runtime reuse checks,
- successful Tika packaged fallback-catalog checks,
- successful real-world Tika EPUB parsing against local Tika 3.2.3 using the compatibility-first `text/plain -> rmeta/json` fallback chain,
- visible in-page Tika runtime install progress wiring in the Qt configuration flow,
- successful MCP tool-schema and headless-import regression coverage,
- successful MCP self-check persistence into the shared AppData area,
- successful MCPB pack/validate/unpack verification for the Windows binary MCP shell,
- `252` passing automated tests on the current MCP-enabled stabilization branch,
- successful Windows EXE packaging.

## Intentional Tradeoffs In The Current Mainline

### 1. `torch` is the stable default runtime

The ONNX route is still not the primary production path for this release.

Why: it still needs additional packaging and model-shaping work before it becomes the clean default.

### 2. The desktop UI prioritizes a complete workflow over feature sprawl

This release deliberately focuses on:

- configuration,
- preflight,
- bootstrap,
- rebuild,
- query,
- watch,
- cleanup,
- export.

It does **not** yet try to ship everything at once.

## Next Priorities

1. finish dual-shell packaging validation for the new MCP line and keep the MCP schema stable,
2. keep improving extension source-directory build UX and richer per-file failure surfacing,
3. continue tightening packaged startup behavior and footprint,
4. keep hardening retrieval quality without regressing the now-stabilized packaged flow,
5. reserve a later line for `Streamable HTTP` after stdio MCP is fully stable.

### 9. Desktop UX must be newcomer-first

The desktop UI is no longer allowed to assume the user already understands RAG terminology or the product workflow.

Current UX rules:

- the first screen must explain the first three actions in plain language,
- recommended defaults must already be filled in,
- first-run critical controls should be visible without requiring the user to hunt through collapsed sections,
- the desktop shell should expose only two top-level tabs: Query for retrieval work and Config for start/settings/data flows,
- missing prerequisites such as local models must surface as clear prompts instead of silent stalls,
- model-download prompts must also provide a manual mirror fallback and the exact local directory for users who need to download files themselves,
- hover tooltips must explain settings and buttons without forcing the user to open external docs,
- the configuration side should stay tabbed and scrollable inside the Config tab, while the Query tab keeps the dominant workspace for search, sorting, details, filtering, and full-context review,
- page-title filtering and sensitive-content redaction belong to the Query workspace because users need to judge and tune them while looking at live hits, not buried in configuration pages,
- every large text panel should support in-place find so users can inspect long snippet, context, and log outputs without launching a second search,
- the full-context view should expose quick page-level jump and counts so large context packs stay navigable after export shaping,
- switching the UI language must also relocalize live status summaries, preflight text, and other runtime labels instead of only translating static widgets.

Why: a local-first desktop tool fails its job if the user has to reverse-engineer the interface before they can trust it.

### 10. UI text and behavior hints live outside the window layout

Current split:

- `ui_i18n.py`: bilingual UI strings and tooltip text,
- `ui_tooltip.py`: hover help behavior,
- `gui.py`: layout, state, background-task orchestration.

Why: language switching, wording upgrades, and future docs-quality polishing should not require invasive changes across the GUI layout code.

### 11. Brand assets are generated locally and packaged explicitly

Current asset policy:

- app icons are generated from a deterministic local script,
- source runs load icon resources from `resources/`,
- EXE builds bundle the same resources and embed the `.ico` into the executable.

Why: the desktop app, taskbar icon, and packaged EXE must present a consistent identity without adding heavy image-tool dependencies.

### 12. Global shared data and per-vault workspace data must be separated explicitly

Current data split:

- `%APPDATA%\OmniClip RAG\shared\` keeps app-level shared material such as logs, shared Runtime payloads, and other cross-vault assets,
- per-vault workspace data remains isolated in vault-scoped data roots for indexes, watch state, and retrieval stores,
- user notes themselves remain outside both of those trees.

Why: shared Runtime reuse and clean packaged upgrades only work when cross-vault shared assets are not mixed into per-vault index directories.

### 13. The selected data root is now the only environment root

Current data-root policy:

- the user-facing `Data directory` setting is the single environment switch,
- the active environment root is tracked through a tiny global bootstrap pointer at `%APPDATA%\OmniClip RAG\bootstrap.json`,
- the bootstrap pointer stores only `active_data_root` and is not treated as user data storage,
- all real user data lives under the selected root:
  - `<data_root>/config.json`
  - `<data_root>/shared/runtime`
  - `<data_root>/shared/cache/models`
  - `<data_root>/shared/extensions_runtime/tika`
  - `<data_root>/shared/logs`
  - `<data_root>/workspaces/...`,
- switching the data directory means switching to another whole environment,
- the product does not automatically migrate runtime payloads, models, indexes, logs, or workspace data between roots,
- users may copy environments manually, and the app only recognizes whichever root is currently selected,
- runtime management, runtime health checks, and pending runtime update application must all follow the active data root instead of silently reading the historical default `%APPDATA%` config path,
- `OMNICLIP_RUNTIME_ROOT` remains a developer-only override and must not redefine ordinary user behavior.

Why: the old model already let most paths follow `data_root`, but the main semantic runtime still had side paths back into the default AppData tree. That made “change data directory” feel unified in the UI while still leaving hidden runtime truth elsewhere. The new rule is simpler and safer: one selected directory equals one whole OmniClip world.

### 14. Missing or disconnected data roots must block startup instead of silently falling back

Current unavailable-root policy:

- if the active data root is missing, unreadable, unwritable, not a directory, or its backing drive is disconnected/locked, the app must treat that environment as unavailable,
- GUI startup must enter an explicit `目录不可用` blocked state,
- the GUI may only offer three actions in that state:
  - `重试`
  - `选择新的数据目录`
  - `退出`,
- headless / MCP / non-GUI entrypoints must fail clearly and immediately instead of silently falling back to a fresh default directory,
- the product must never auto-create a new hidden environment in `%APPDATA%` just because the previously selected drive disappeared.

Why: local-first software cannot quietly hop to a new storage root without destroying the user's mental model. If an encrypted disk is locked or an external drive is missing, the honest behavior is to say so and block, not to pretend the app started normally against a different world.

### 15. Persistent file logging must start only after the active data root is resolved

Current logging-bootstrap policy:

- the process may install exception hooks early, but it must not initialize persistent file logging before the real active data root has been resolved and validated,
- startup-time diagnostics before root resolution are limited to stderr / console output or temporary in-memory state,
- file logs may only be created under `<active_data_root>/shared/logs` after the active root is known to be usable,
- if the active data root is unavailable, startup may surface the error in UI or stderr, but it must not leave stray fallback log files under the default AppData tree.

Why: if persistent logging starts too early, the app leaks partial startup traces into the wrong directory and undermines the whole “one selected directory equals one environment” rule.

### 13. Runtime is a shared sidecar, not an EXE-folder singleton

Current Runtime policy:

- the public Windows package stays lean and still does not bundle `torch` or `sentence-transformers`,
- the preferred install / repair target for packaged builds is now `%APPDATA%\OmniClip RAG\shared\runtime`,
- packaged startup may still reuse a healthy legacy Runtime from the current EXE folder, a manually moved `runtime/`, or a sibling `OmniClipRAG-v*/runtime` directory,
- new component registrations prefer relocatable relative paths, while stale absolute-path manifests are salvaged when possible,
- packaged startup restores the external Runtime search paths before probing `torch` or `sentence_transformers`.

Why: Runtime should survive packaged version drift. A user-installed Runtime is a sidecar capability layer, not disposable baggage tied to one exact EXE folder name.

### 14. Tika format visibility must not depend on local Tika installation

Current Tika picker policy:

- if an installed Tika server JAR is available, parse its format catalog first,
- otherwise fall back to the packaged suffix catalog bundled inside the app,
- curated defaults are only the last fallback,
- `pdf` remains permanently excluded from the Tika picker because PDF follows its own dedicated parse/index/query chain.

Why: the user must be able to understand the Tika format universe before committing to a Runtime install. Format visibility is product UX, not a side effect of a successfully installed sidecar.

### 15. Late-stage build optimizations are independent performance tracks

Current build-performance work is intentionally split into two groups:

- core usability and stability work that is already in the main path,
- heavier throughput work that is still optional and can be deferred safely.

The main-path work now includes:

- adaptive encode/write batch control,
- phase-aware ETA tracking,
- a bounded single-writer rebuild pipeline for vector indexing,
- split accounting for encode vs row-materialization vs LanceDB write time,
- write-backlog-aware tuning and task-detail/build-history telemetry.

The remaining items such as deeper LanceDB profiling and narrower late-tail specialization are **not** prerequisites for rebuild correctness. They are independent performance tracks that matter mainly when users rebuild very large vaults frequently.

Why: this keeps the product boundary clear. Not shipping those later-stage optimizations must never be interpreted as "full rebuild is broken" or "daily search quality depends on them". They only affect how far the product pushes the throughput ceiling on huge full rebuilds.

### 16. Lean packaged builds must preserve user-installed sidecars

Current packaging rule:

- EXE builds and GitHub releases should keep Runtime payloads, Tika JARs, JREs, model caches, and user data outside the bundled app,
- source control continues to ignore `runtime/`, `dist/`, and other large local artifacts,
- a new packaged version should prefer reusing a healthy existing Runtime rather than forcing a multi-GB reinstall.

Why: the product promise is "lean shell plus durable local sidecars," not "every upgrade is a full reinstall."

### 13. Long-running desktop actions must expose visible progress, not silent waiting

Current desktop behavior:

- `Check disk space` and `Download model` now publish a running state in the Start tab,
- the UI shows elapsed time,
- the UI shows a plain-language estimated duration,
- model bootstrap short-circuits when the model is already present and passes a local integrity check.

Why: for local-first desktop tools, silent waiting looks like a freeze. Even when the task is indeterminate, the user must still see that work is in progress and what kind of time budget to expect.

### 14. Duplicate Logseq block ids must not crash indexing

Current fault-tolerance rule:

- the first occurrence of a duplicated `id:: UUID` keeps the canonical `block_id`,
- later duplicates are demoted to plain chunks instead of aborting the whole run,
- demoted chunks keep duplicate metadata for later diagnosis,
- rebuild and watch both surface duplicate-id events in the desktop activity log.

Why: real vaults occasionally contain copied or merge-conflicted Logseq ids. A single dirty block must not take down the whole index.

### 15. Desktop layout must be adjustable and stateful

Current desktop layout policy:

- the main window uses draggable split panes instead of one rigid stacked page,
- the left workspace area and the right search/detail area can be resized independently,
- the result list and detail tabs are also resizable,
- window geometry and pane positions are persisted in config and restored on the next launch,
- zero or corrupt old pane values are ignored and replaced with safe defaults.

Why: the app is a desktop workstation, not a fixed dialog. Users need to adapt it to their monitor shape and keep that layout across sessions.

### 16. Query result views must keep the UI interactive under repeated sorting and selection

Current desktop-result policy:

- result rows use stable `chunk_id` identities instead of index-based row ids,
- checkbox toggles update only the affected row state instead of deleting and recreating the whole `Treeview`,
- heavy full-context rebuilds may be deferred briefly when the result set is large so quick repeated clicks do not lock the main thread,
- log output appends incrementally instead of rewriting the entire log text area every time,
- background queue draining is batch-limited so rebuild/watch progress updates do not starve normal UI input,
- the result toolbar now includes a page-level sort mode that groups fragments by page and ranks pages by the average score of their visible fragments,
- clicking the page-sort button again or using any table heading exits page sort and returns to the normal flat result ordering workflow.

Why: the desktop query surface is touched far more often than setup screens. If sorting, ticking, or progress updates monopolize the Tk main thread, users read that as a frozen product even when retrieval quality is correct.

### 17. A ready local model must never trigger fresh network access

Current vector-loading rule:

- if the selected model already passes the local integrity check, search and rebuild load it strictly from the local cache,
- `snapshot_download()` is used only when the local model is genuinely missing or incomplete,
- loaded embedders are cached in-process so a bootstrap followed by rebuild does not pay the model-load cost twice.

Why: once a local-first desktop app says the model is ready, later tasks must not fail because of SSL, proxy, or Hugging Face availability noise.

### 17. Full rebuild must survive interruption and support explicit resume

Current rebuild-resilience rule:

- each vault workspace stores a `rebuild_state.json` marker under its own state directory,
- the state records the manifest, completed files, readable files, current phase, and duplicate-id count,
- a normal new rebuild resets state first,
- an interrupted rebuild can resume only when the vault path and relevant vector settings still match and the file manifest is unchanged,
- startup prompts the user to continue or discard unfinished rebuild state,
- discarding also clears partial index state so the workspace does not keep ambiguous leftovers.

Why: local indexing jobs can be interrupted by app close, crashes, or power loss. The product must recover deterministically instead of forcing the user to guess whether the index is trustworthy.

### 18. Preflight must estimate time as well as disk space

Current preflight rule:

- preflight now estimates both free-space requirements and a conservative first-build time budget,
- if the model is missing, preflight also estimates extra first-download time,
- the GUI surfaces these numbers directly in the context tab and task panel wording.

Why: for a local desktop workflow, “can it fit?” and “how long will it take?” are both first-run gating questions.

### 19. Full rebuild must support real pause/resume, not just restart-after-abort

Current rebuild-control rule:

- the desktop task panel exposes a pause/resume control only while a full rebuild is running,
- pause points exist in file parsing, rendered-text expansion, and vector batching,
- paused rebuilds keep their persisted rebuild state,
- closing the app while paused still falls back to the existing rebuild-resume flow on the next launch.

Why: long local indexing runs compete with normal desktop work. Users need a safe way to yield CPU temporarily without throwing away an in-flight full rebuild.

### 20. Paused rebuild timing must reflect real paused time

Current rebuild-timing rule:

- elapsed time stops increasing while rebuild is paused,
- ETA keeps the last credible estimate instead of dropping to nonsense values,
- the desktop task panel shows progress percentage and paused-state wording explicitly,
- cancellation is a first-class rebuild outcome, not an unhandled error path.

Why: once pause exists as a real control, time reporting has to match the user's expectation of what paused means.

### 21. Clipboard export must be Unicode-safe on Chinese Windows

Current clipboard rule:

- context packs are normalized to Windows newlines,
- clipboard writes use a Unicode-safe byte path instead of the process default console encoding,
- symbols that are common in formatted context packs must not fail because the host code page is `gbk`.

Why: the core workflow of this product is copying context into other AI tools. Clipboard export is not optional plumbing; it is the product bridge.

### 22. Query review must be user-steerable after retrieval

Current query-review rule:

- each hit exposes a relevance score, hit reason, and a focused matched excerpt,
- the user can filter low-confidence hits with a score threshold,
- the user can include or exclude individual hits from the generated context pack,
- the details pane separates matched excerpt from full chunk so the result list is easier to audit.

Why: if retrieval output is not reviewable and editable, users cannot trust the context pack they are about to hand to an external AI.

### 23. GPU detection must be honest about the actual backend

Current acceleration UX rule:

- the settings panel can show detected NVIDIA hardware separately from runtime readiness,
- `auto` remains the safe default,
- `cuda` is only presented as truly usable when the active runtime confirms PyTorch CUDA support,
- there is no fake generic-GPU mode that silently falls back to CPU while pretending otherwise.

Why: performance hints are useful, but false promises about GPU acceleration create more confusion than a conservative capability report.


## 2026-03-10 Query Runtime Shaping

### Decisions
- Same-page hit selection is no longer allowed to live in `gui.py` or as ad-hoc helpers inside the service layer. It now belongs to a dedicated query-runtime module.
- `query_runtime.py` owns three post-retrieval responsibilities: duplicate suppression, same-page novelty selection, and query-limit recommendation.
- Query-limit guidance is advisory, not automatic. The app may recommend a range based on live runtime history, but it must not silently rewrite the user's configured value.
- The GUI only consumes structured `QueryResult -> QueryInsights -> QueryLimitRecommendation` payloads. It may render hints and tooltips, but it must not recalculate retrieval policy.

### Why
- Retrieval quality should be testable without spinning up the desktop UI.
- Same-page de-duplication is the main lever for reducing user self-filtering cost, but it is also easy to overdo and accidentally hide complementary evidence.
- Query-limit tuning depends on real runtime behavior, so the product should learn from elapsed time and candidate pressure instead of forcing users to brute-force the right number manually.

### Implementation Notes
- `models.py` now carries `QueryResult`, `QueryInsights`, and `QueryLimitRecommendation` so the service/UI contract stays explicit.
- `service.py::query()` returns a structured result object and records lightweight runtime telemetry into `query_runtime.json` under the per-workspace state directory.
- `query_runtime.py::select_query_hits()` keeps the strongest same-page evidence, suppresses obvious overlap, and preserves complementary fragments when they add new information instead of repeating the same branch.
- `gui.py` now only renders the recommended range and the reason string; the actual novelty and recommendation logic stays backend-only.
- The query-limit tooltip now explains both the current display semantics and that the same knob will become the reranker candidate-pool size once reranking is added later.

## Packaging Hygiene

The repository should keep only one formal Windows desktop deliverable at a time:

- keep `dist/OmniClipRAG/` as the release artifact,
- remove stale one-file EXEs, smoke-build folders, and temporary build directories after validation.

Why: duplicate package shapes increase user confusion and make issue reports harder to reason about.

### 24. Build ETA should learn from real workspace history

Current timing rule:

- precheck and rebuild ETA first use a conservative static profile,
- once a workspace has finished one or more rebuilds, later estimates reuse recent indexing / rendering / vectorizing timings from that same workspace,
- the ETA blends static expectations with live observed progress instead of trusting either one blindly.

Why: a purely static estimate is too abstract, while a purely live estimate is unstable at the start of a run. Real local history gives later predictions a much better baseline.

### 25. Runtime acceleration should default to `auto`

Current acceleration rule:

- the persisted device default is `auto`, not hard-coded `cpu`,
- `auto` resolves to `cuda` only when the active runtime really confirms CUDA availability,
- the GUI may still show detected NVIDIA hardware separately from actual runtime readiness.

Why: users should not have to hand-tune the common case, but the app also must not pretend GPU acceleration exists when the active runtime cannot use it.

### 26. Lean Windows builds must still carry `pyarrow.libs`

Current packaging rule:

- the main release stays lean and does not bundle heavy optional AI runtimes,
- but the packaged desktop app must still include the non-optional `pyarrow.libs` directory required by `lancedb` / `pyarrow`,
- startup validation should treat a missing `pyarrow.libs` folder as a broken build, not as a user-runtime problem.

Why: `lancedb` and `pyarrow` are part of the core desktop retrieval stack. They are not optional runtime add-ons like `torch`.

### 27. System CUDA and app runtime readiness must be reported separately

Current acceleration-reporting rule:

- detecting an NVIDIA GPU or a system `nvcc` installation does not mean the packaged app itself can already run CUDA,
- the desktop summary must tell the user when the system has CUDA but the lean packaged app still lacks its own PyTorch / sentence-transformers runtime,
- runtime-missing failures must produce a direct install command instead of a raw traceback,
- the guidance must explicitly say that CPU mode can still use LanceDB after installing the `cpu` runtime profile,
- `disabled` is only a temporary bypass for users who want to turn vector retrieval off completely.

Why: users reasonably assume “nvcc works” means the app should already use the GPU. The product must explain the missing app-local runtime boundary explicitly.

### 28. Plan documents under `plans/` are execution baselines, not speculative notes

Current documentation rule:

- `plans/检索优化计划.md` records the retrieval-quality work that has already been delivered into the mainline.
- `plans/建库性能优化计划.md` records the full-rebuild performance work that is already in the mainline plus the explicit boundary of what is intentionally out of scope for now.
- README and changelog entries should stay aligned with those plan files so a reader does not mistake independent future work for a missing core capability.

Why: the project uses the plan files as durable engineering memory. If the higher-level docs drift away from them, users can wrongly conclude that rebuild or retrieval is still incomplete when the missing items are actually optional later-stage work.
## 2026-03-09 RAG Output Contract Refresh

### Decisions
- Retrieval remains chunk-first, but clipboard export is now source-faithful evidence, not the internal search text.
- Query ranking now merges lexical candidates and vector-only candidates before scoring, so semantic hits are no longer dropped whenever FTS/LIKE produced any result.
- Context packs group by note title and emit `笔记片段1/2/...` blocks with the original Markdown shape preserved as much as possible.
- Logseq block refs `((uuid))` are resolved to readable text; embeds `{{embed ((uuid))}}` replay the embedded block tree with its source ancestry and children instead of leaking UUIDs.
- The fixed tail prompt/protocol was removed from the default export path. Retrieval data and prompt templates are now separate concerns.
- Clipboard export applies configurable redaction before output. Core secret redaction is enabled by default; extended privacy redaction and custom rules are opt-in.

### Why
- Whole-page export caused lost-in-the-middle failures, token waste, and accidental leakage of unrelated secrets.
- The previous pipeline embedded and ranked one representation, then exported a different and much noisier representation, which made the product feel worse than plain keyword search.
- Users trust the tool when the exported snippet visibly matches the source note they remember writing.

### Implementation Notes
- `parser.py` now persists parent chunk linkage and subtree line ranges so the service can replay original Markdown from disk.
- `service.py` still builds normalized `rendered_text` for FTS/vector search, but query hydration now reopens the source file and renders `display_text` for export/preview.
- Preview panes should show the same `display_text` that the clipboard receives, with `rendered_text` reserved for ranking/index internals.
- Bullet-heavy Markdown files without `id:: UUID` should still be treated as outline notes when their structure is predominantly list-based; otherwise whole-page fallback destroys RAG precision.
- Single-character queries should bias toward lexical retrieval and skip vector recall, because semantic embeddings at that length create more noise than value.
- The visible `0-100` relevance score is an engineered fusion score, not a probability. It combines lexical/title/path/body hits, FTS rank, LIKE hits, vector similarity, and length/coverage penalties.
- Page-filter rules are persisted as enabled/disabled regex entries so noisy page patterns can be saved once, toggled later, and still stay out of query results, snippet details, and full-context export when enabled.

## 2026-03-09 Hot Watch Hardening

### Decisions
- Incremental reindex is no longer allowed to delete old SQLite rows before the changed file has been parsed successfully.
- Watch mode now treats filesystem events as hints only; the real source of truth is `current vault snapshot` vs `indexed manifest` diffing.
- Changed files must pass a stability window before reindex; missing files must pass a delete-confirmation window before they are removed from the index.
- When the vault root becomes temporarily unavailable, watch mode must enter an offline guard state and freeze destructive updates instead of interpreting the vault as empty.
- SQLite remains authoritative during watch updates; vector writes may lag and are tracked as dirty state for later repair.
- Watch recovery state lives in `watch_state.json` under the per-vault workspace state directory, not in the program directory.

### Why
- Editors, sync tools, and encrypted drives routinely produce short windows where a file is half-written, locked, renamed, or temporarily invisible.
- The previous `delete first, parse later` flow could destroy still-valid index data during those transient states.
- Power loss or process crashes during watch updates should degrade to a repairable state, not leave the user guessing whether the index is trustworthy.

### Implementation Notes
- `service.py` now parses changed files first, preserves old index rows on parse/read failure, and only swaps a file after successful parse.
- Watch polling and watchdog paths both run through the same stability buffer so the product behavior stays consistent across backends.
- The watch buffer tracks two clocks: a stable-write timer for changed paths and a grace timer for missing paths.
- `watch_state.json` tracks `dirty_paths`, `dirty_vector_paths`, `dirty_vector_chunk_ids`, and whether the vault is currently offline.
- Render refreshes and vector writes are recoverable: failed render/vector work is left marked dirty and replayed later instead of forcing an all-or-nothing crash.
- GUI activity logs must surface vault-offline, vault-recovered, repair, and retry events so watch behavior is auditable in normal desktop use.

## 2026-03-10 Large-Vault Batch Safety

### Decisions
- SQLite writes that depend on `IN (...)` placeholders must always be batched against the runtime variable limit instead of assuming desktop-scale payload sizes.
- Full rebuild rendering must flush rendered chunks in bounded batches; it may not accumulate the entire vault's rendered payload in memory before writing.
- Full rebuild vectorization must consume rendered documents as a stream so vault size is no longer coupled to one giant in-memory list.
- Incremental vector sync for large single pages must upsert/delete in bounded batches as well, because tens of thousands of chunks can come from one page.

### Why
- Large vaults can exceed SQLite's variable cap during `DELETE ... IN (...)` or `SELECT ... IN (...)`, which crashes the build near the end even if parsing succeeded.
- Some real pages contain tens of thousands of blocks, so 'single page' does not mean 'small batch'.
- The safe target is not 'works for today's vault' but 'still behaves predictably when vault/page counts are orders of magnitude larger'.

### Implementation Notes
- `storage.py` now resolves SQLite's live variable limit and batches all high-risk path/chunk/block-id queries and deletes.
- `update_rendered_chunks()` rewrites FTS rows in batches inside one transaction instead of emitting one unbounded placeholder list.
- `service.py::_refresh_rendered()` now streams rows and flushes rendered text every fixed batch instead of waiting for the full render pass to finish.
- `vector_index.py::rebuild()` now accepts streamed iterables with an explicit `total`, and vector deletes are chunked to avoid oversized deletion predicates.




## 2026-03-10 Build Throughput Control

### Decisions
- Full rebuild performance tuning is now backend-owned. The desktop UI exposes only a coarse peak target (`30% / 50% / 90%`), while the actual batch sizing and safety decisions live outside the window layer.
- CPU, GPU, and memory must be coordinated together during vector rebuilds. The app is not allowed to blindly maximize one resource while starving the others or pushing the machine into avoidable instability.
- The peak selector is a target envelope, not a literal hard cap. The controller aims for quiet / balanced / peak behavior but may back off aggressively on pressure or OOM.
- Vector encode batch size and LanceDB write batch size are now separate tuning knobs. GPU-friendly encode sizing and storage-friendly write sizing are not forced to be identical.
- Runtime safety wins over theoretical throughput. Any OOM or pressure event must degrade to a smaller batch or a safe fallback instead of crashing the rebuild.

### Why
- Large Logseq vaults can spend most of their rebuild time in the late vector stage while CPU and GPU both remain underutilized, which means the current bottleneck is orchestration rather than pure model speed.
- A desktop app must let users trade off speed vs foreground usability without exposing dozens of low-level performance settings.
- The app already targets very large vaults and huge single pages, so performance tuning must stay bounded, observable, and recoverable under real Windows desktop conditions.

### Implementation Notes
- `build_control.py` owns resource sampling and adaptive batch control. It samples Windows CPU and memory, queries NVIDIA usage through `nvidia-smi` when CUDA is active, and produces profile-aware tuning snapshots.
- `vector_index.py::rebuild()` now keeps encode batching and write batching separate, buffers rows before LanceDB writes, and reports tuning snapshots in task progress payloads.
- The controller currently adjusts three things: encode batch size, write batch size, and cooldown after OOM / pressure events.
- `timing.py` now owns a `BuildEtaTracker` that keeps recent per-stage progress windows and blends them with static history instead of trusting one whole-run average.
- Vector tail speed is now written into `build_history.json`, so the next build can start with a more realistic estimate for the expensive late vector stage.
- The GUI only persists and renders the build peak profile plus live tuning summaries. It does not decide when to expand or shrink batches.
- The current implementation intentionally stays single-process plus a bounded single writer. It improves throughput through adaptive batching, queue-aware tuning, and a stable encode/write overlap path without introducing aggressive multi-writer risk.
- Future performance work, if needed later, should be treated as a new independent topic focused on narrower LanceDB tail profiling and specialty late-stage tuning, not as a prerequisite for rebuild correctness.

## 2026-03-10 Retrieval Optimization Delivery

### Decisions
- Retrieval shaping is now split into three backend-only modules: `retrieval_policy.py`, `query_runtime.py`, and `reranker.py`. The GUI consumes results; it does not decide ranking strategy.
- Mixed retrieval now starts from a typed `QueryProfile`, so single-character queries, concept queries, and natural-language queries no longer share one blunt retrieval path.
- Same-page post-selection is novelty-based instead of page-penalty-based. The system suppresses obvious overlap but keeps complementary evidence from the same page.
- Reranking is optional, manually bootstrapped, and failure-safe. It is an enhancement layer, not a prerequisite for querying.
- AI collaboration export is a separate output mode, not a hard-coded tail prompt inside every context pack.

### Why
- The highest user cost was no longer "searching" but manually deciding which same-page fragments to keep.
- Query quality improvements must remain testable without launching the desktop UI.
- Optional heavy features such as a cross-encoder reranker must never destabilize the default local-first workflow.

### Implementation Notes
- `retrieval_policy.py` now owns query intent typing, candidate-pool sizing, lexical/semantic fusion, and hydration-pool sizing.
- `query_runtime.py` now owns same-page novelty selection, duplicate suppression, and query-limit recommendation from runtime history.
- `reranker.py` wraps `BAAI/bge-reranker-v2-m3` behind a replaceable interface, supports batching, truncation, CUDA OOM recovery, batch-size reduction, CPU fallback, and safe skip behavior.
- `service.py::query()` now returns a structured `QueryResult` with `QueryInsights`, recommendation payloads, and optional reranker outcomes.
- `gui.py` only renders recommendation hints, reranker settings, and export-mode controls; it does not recompute backend policy.
- `query_runtime.json` stores lightweight runtime samples so the app can recommend a practical query-limit range without silently rewriting user settings.
- `ai-collab` export mode appends a minimal collaboration note only when explicitly enabled.

### 9. Windows background helper processes must never steal focus

GPU/runtime probing helpers such as `nvidia-smi`, `nvcc`, and clipboard bridge commands must run through a hidden subprocess wrapper on Windows.

Why: repeated console flashes during rebuilds are user-visible regressions, can steal focus from the desktop app, and make large-vault indexing feel unstable even when the worker itself is still healthy.



## 2026-03-10 Desktop UI Performance And Display Preferences

### Decisions
- Routine UI toggles such as quick-start expansion and advanced options must stay local to their own card. They are not allowed to trigger a full `root` rebuild anymore.
- Any layout work driven by Tk `Configure` events must be coalesced through deferred callbacks instead of recalculating wrap lengths or canvas widths on every drag frame.
- Query feedback in the desktop window must reflect real backend query stages. Static "searching" text without backend progress is no longer acceptable for the query page.
- Theme mode and UI text scaling are first-class persisted preferences. They belong in config, not as one-off runtime tweaks.

### Why
- The largest visible lag was no longer in retrieval logic; it came from rebuilding the whole widget tree for small visibility toggles and from synchronously handling high-frequency layout events during pane dragging.
- Users need immediate confidence about whether a query is idle, blocked, running, or finished. The absence of a clear query-state surface made the app feel frozen even when work was progressing.
- Long sessions in large vaults are desktop-heavy workflows, so readability and dark/light preference handling are part of usability, not cosmetic extras.

### Implementation Notes
- `gui.py` now keeps quick-start and advanced sections mounted and only switches them with `grid()` / `grid_remove()`.
- Scroll-canvas width sync, responsive wrap recalculation, and similar `Configure`-driven updates now go through deferred UI callbacks so divider drags do not fan out into full-frame layout churn.
- The query page now renders a dedicated status banner with idle / blocked / running / done modes, and its running state is fed by `service.py::query(..., on_progress=...)` stage payloads.
- Query task progress is also reflected in the shared background-task panel so the banner and task panel stay consistent.
- UI theme (`system` / `light` / `dark`) and UI scale percent are persisted in config and applied through the shared style/bootstrap path.

## 2026-03-11 Desktop UI Interaction Throttling

### Decisions
- Root-window resize, Win snap, and pane-sash dragging are now treated as one class of high-frequency UI interaction. Heavy layout sync is delayed until the interaction settles instead of running every frame.
- Notebook tab switching must refresh only the newly visible subtree. It is not allowed to trigger broad hidden-tab layout work.
- Responsive wrap handling is parent-group based rather than one binding per label. Multiple wrapped labels inside the same card now share one configure pipeline.
- Scroll-canvas sync must be visibility-aware. Hidden tabs keep pending width / scrollregion work queued and only flush it when they become visible.

### Why
- The remaining lag was no longer caused by whole-window rebuilds alone. It came from resize storms where many labels and canvases all responded to `Configure` independently.
- Win snap and edge-resize behavior can transiently squeeze Tk containers into unstable intermediate sizes. If every canvas and wrapped label reacts immediately, the user sees blank frames and jitter.
- Tab switches felt visually unstable because newly selected tabs had to catch up on width and scrollregion state after the fact.

### Implementation Notes
- `gui.py` now tracks a short-lived UI interaction window for root `<Configure>`, notebook tab changes, and pane drags, then runs a visible-only layout refresh after the interaction settles.
- Responsive wrap widgets are registered into `responsive_wrap_groups`, so each parent frame owns one deferred wrap recalculation pass instead of many parallel callbacks.
- Scrollable canvases now store sync state in `canvas_sync_states`, skip redundant width / scrollregion writes, and flush hidden-tab work on `Map` or selected-tab refresh.
- `main_tabs`, `left_tabs`, and result-detail `tabs` all register notebook-local layout refresh hooks so switching pages refreshes only the selected content tree.


## 2026-03-11 Qt UI Migration Phase 1-2

### Decisions
- The desktop app now has a unified entry layer. New launches default to the Qt shell, while the Tk UI remains available as a separately runnable legacy surface.
- The first Qt delivery is intentionally limited to the new shell and the query workspace. Settings and secondary tools stay out of scope until the query path proves stable in real use.
- Query results in Qt must use the Model/View stack (`QTableView + QAbstractTableModel`). The migration is not allowed to recreate the old pattern of coupling retrieval data to ad-hoc widget trees.
- Long-running query work must execute in a worker thread and report progress through Qt signals. The UI thread is reserved for rendering, selection, and lightweight state updates.

### Why
- The previous Tk bottlenecks came from layout churn and high-frequency interaction storms in the most frequently used query surface. Revalidating the query path first lowers migration risk before moving the rest of the product.
- Keeping the legacy UI alive during the first Qt phases preserves a safe rollback path while the new shell hardens.
- Model/View and threaded workers are the foundation for the long-term goals: smoother sorting, more stable splitters, and a UI that stays responsive under retrieval load.

### Implementation Notes
- New UI code lives under `omniclip_rag/ui_next_qt/`; the legacy adapter lives under `omniclip_rag/ui_legacy_tk/`; shared query-facing helpers live under `omniclip_rag/ui_shared/`; the launch switchboard lives under `omniclip_rag/app_entry/`.
- `launcher.py` now routes through the shared desktop entry. `--ui legacy` starts the Tk window explicitly; the default path opens the Qt shell and falls back to Tk if Qt startup is unavailable.
- `ui_next_qt/main_window.py` owns the phase-1 shell: header, top-level tabs, persisted geometry, persisted splitter state, and the explicit "open legacy UI" bridge.
- `ui_next_qt/query_workspace.py` owns the phase-2 query surface: status banner, query controls, worker lifecycle, page-average sorting toggle, context selection summary, detail tabs, and context rebuild flow.
- `ui_next_qt/query_table_model.py` provides checkbox selection, stable sorting, page-average aggregation sort, and restore behavior through a dedicated table model instead of widget mutation.
- `ui_next_qt/workers.py` wraps `OmniClipService.query(...)` inside a `QObject` worker hosted by `QThread`, and the query banner consumes stage updates from the backend progress callback.
- Qt-only persisted UI state currently lives in config as `qt_window_geometry`, `qt_query_splitter_state`, and `qt_results_splitter_state` so the new shell can evolve without destabilizing Tk layout memory.
- Phase 1-2 intentionally leave page-blocklist editing, sensitive filtering dialogs, and the full settings surface on the legacy path. The Qt query workspace exposes explicit bridges back to the legacy UI for those not-yet-migrated tools.

## 2026-03-11 Qt UI Migration Phase 3-4

### Decisions
- The Qt shell now owns both the query workspace and the full config workspace. The legacy Tk UI remains available only as an explicit fallback surface, not as the primary path for settings or filtering tools.
- Query splitters must remain user-draggable even when their children have rich nested layouts. The splitter host widgets therefore use an `Ignored` vertical size policy and restore persisted state only when there is real pending state to apply.
- Query blocking, filter editing, task logs, and theme / scale preferences are routed through explicit Qt signals between workspaces instead of direct cross-widget mutation.
- Page-title blocklist editing and sensitive-content filtering are now first-class Qt dialogs. They persist back into shared config and can be launched from either the query workspace or the config workspace.

### Why
- Phase 1-2 proved the Qt query stack was smooth, so keeping config and filtering on Tk would only extend the split-brain period and increase migration risk.
- The locked-splitter bug came from two combined issues: rich child widgets still advertised large minimum size hints to `QSplitter`, and the query workspace reapplied default/saved splitter state on later show events. Both had to be fixed structurally.
- Shared blocking and log signals let long-running tasks, hot-watch mode, and query UX stay consistent without coupling the query view to build / watch internals.

### Implementation Notes
- `ui_next_qt/config_workspace.py` now contains the phase-3 config surface: Start, Settings, UI, Retrieval, and Data tabs, along with threaded service tasks, rebuild pause/cancel controls, watch lifecycle, and config persistence.
- `ui_next_qt/filter_dialogs.py` and `ui_next_qt/filter_models.py` provide the phase-4 auxiliary tools for page-title blocklist editing and sensitive-filter rule editing.
- `ui_next_qt/main_window.py` now wires the config workspace into the main shell, forwards runtime config changes to the query workspace, applies theme/scale updates app-wide, and routes filter-dialog requests from the query page into the shared Qt tools.
- `ui_next_qt/query_workspace.py` now keeps user splitter changes stable across hide/show cycles, only restores saved/default splitter geometry when pending state exists, and uses splitter-child size policies that allow real drag freedom.
- `tests/test_qt_ui.py` now covers splitter persistence, config-workspace persistence, and filter-model behavior so later migration phases can refactor safely without regressing the Qt core.

## 2026-03-11 Qt Startup Hardening

### Decisions
- `python launcher.py` must be a first-class launch path. It now bootstraps local `.packages` / `.vendor` dependencies itself instead of relying on the PowerShell wrapper.
- Interactive Qt startup is no longer allowed to honor a stray `QT_QPA_PLATFORM=offscreen` unless it is explicitly whitelisted for diagnostic runs.
- The config workspace's initial status refresh is no longer allowed to run synchronously on the UI thread during first paint. Startup status loading now runs later and off-thread.
- Restored Qt window geometry must be validated against the current screen layout. If the saved window is fully off-screen, the app recenters it instead of silently restoring an invisible window.
- Qt startup failures and import failures must print full diagnostics to stderr so a blocked launch is observable from the terminal.

### Why
- The migration introduced a new default launch path, but `python launcher.py` was still weaker than `scripts/run_gui.ps1` because it did not bootstrap the repo-local dependency folders.
- A hidden or off-screen window is operationally indistinguishable from a hung startup to end users.
- Even lightweight status probes can be enough to delay the first visible frame when they happen at the wrong point in the event loop.
- Silent fallback or swallowed startup issues create long debugging loops because the terminal gives no trustworthy clue about what really happened.

### Implementation Notes
- `launcher.py` now prepends the repo root, `.packages`, and `.vendor` to `sys.path` before importing the desktop entry.
- `ui_next_qt/app.py` now installs a terminal-visible exception hook, normalizes `QT_QPA_PLATFORM`, raises/activates the main window, and schedules config status loading after the first frame.
- `ui_next_qt/config_workspace.py` now loads its initial `status_snapshot()` via `ServiceTaskWorker` instead of synchronously inside the first UI event turn.
- `ui_next_qt/main_window.py` now verifies restored geometry against the available screen rectangles and recenters the window if needed.
- `app_entry/desktop.py` now prints full traceback details when Qt import fails before falling back to the legacy UI.
## 2026-03-11 Qt Phase 3-4 Stabilization Pass

### Decisions
- Preflight estimation is now treated as a real staged background task, not a silent prerequisite before rebuild. Vault scanning and Markdown parsing must both emit progress payloads and obey the same cancel / pause control channel used by rebuild tasks.
- Rebuild pause and cancel are not allowed to leave the task panel in an indeterminate marquee state. The progress bar must freeze into a determinate visual snapshot as soon as the user pauses or confirms cancellation.
- The Qt shell should stay visually compact like a classic desktop tool. Header cards and top-level layout margins were tightened, and duplicated padding in the header stylesheet was removed.
- Tooltip migration is part of functional parity, not optional polish. Query controls, in-panel text search, vault actions, and filter dialogs now carry the guidance that existed in the legacy UI.

### Why
- Users interpreted long preflight scans as a deadlock because the previous Qt task panel stayed on "waiting to start" until the rebuild proper began.
- A cancel flag that only stops the worker loop but leaves the progress bar animating still feels broken in practice.
- Qt widgets are fast enough, but loose margins and missing hover states quickly make the new shell feel less mature than the old UI.
- Legacy tooltips encoded important operational knowledge for first-run tasks, filters, and search workflow; dropping them increased onboarding friction.

### Implementation Notes
- `preflight.py` now emits `preflight_scan` and `preflight` progress stages, and both the directory walk and file-parse loop honor pause / cancel events by raising `BuildCancelledError` promptly.
- `service.py` forwards `on_progress`, `pause_event`, and `cancel_event` into `estimate_space(...)`, so preflight and rebuild share one interruption model.
- `ui_next_qt/config_workspace.py` now renders dedicated preflight progress text, freezes the progress bar on pause/cancel, and restores the idle task panel copy after background work stops.
- `ui_next_qt/theme.py`, `ui_next_qt/main_window.py`, and `ui_next_qt/config_workspace.py` now use tighter top-level spacing, and the combo-box popup explicitly styles hover / selected rows so the highlight logic matches desktop expectations.
- `ui_next_qt/query_workspace.py` now restores the missing query-side tooltips, including query actions, page filtering, context jump, and in-panel text search.
- `tests/test_qt_ui.py` now covers preflight cancellation, quick-start content presence, pause/cancel progress freezing, tooltip migration, and combo-box hover stylesheet generation.
## 2026-03-11 Qt Task Sequencing And Header Memory

### Decisions
- Qt task completion now follows the legacy Tk semantics: a background task is considered finished only after the worker thread fully stops, `busy` is cleared, and the task panel is reset. Success / cancel / error handlers then run afterward.
- Preflight estimation now exposes its post-parse tail as a real `preflight_finalize` stage. Measuring workspace state and model-cache size is no longer allowed to happen silently after the visible file scan reaches 100%.
- Preflight cancellation must remain effective during the final local-cache sizing pass, not only during the vault scan / parse loop.
- The Qt shell header can now be collapsed and the choice persists in config as `qt_header_collapsed`.

### Why
- The previous Qt flow ran success callbacks before the old task had fully released `busy`. That broke chained operations such as “prepare model, then automatically continue into rebuild”, because the follow-up task immediately hit the busy guard and stopped.
- On real vaults, the expensive part after the visible Markdown scan was often the directory-size walk over workspace state and shared model cache. Without a separate stage label, users reasonably read this as a hang.
- A cancellation path that cannot interrupt the final cache-size walk still feels like a dead cancel button.
- The top header became more compact, but users still needed a persistent way to reclaim that vertical space completely when the guide copy was no longer needed.

### Implementation Notes
- `ui_next_qt/config_workspace.py` now stores task outcomes first and dispatches them only from `_on_task_finished()`, after clearing `busy`, stopping the task timer, and releasing the worker/thread references.
- `preflight.py` now caps the parse stage below 100%, emits `preflight_finalize`, and makes `_directory_size(...)` obey the same pause / cancel controls used elsewhere in the rebuild pipeline.
- `ui_i18n.py` now includes finalize-stage copy and header collapse / expand labels.
- `ui_next_qt/main_window.py` now owns a small persistent header toggle button and writes its state back through config save flow.

## 2026-03-11 Qt Start-Page State Closure

### Decisions
- The Start page must treat successful preflight and successful rebuild as first-class UI state transitions. Returned rebuild stats must be merged into the visible status snapshot immediately instead of waiting for a later manual refresh.
- Whenever a background stage reports a known `current/total`, the Qt task progress bar must expose the same live numbers in its on-bar text. A moving bar with frozen counts is treated as broken feedback.
- The legacy Tk shell is now single-instance from both the Qt bridge and the direct legacy entry path. Repeated clicks may not spawn duplicate fallback windows.

### Why
- Users read a stale "index not built yet" chip or frozen count text as a failed rebuild, even when the backend completed successfully.
- The compatibility button exists as a safety valve; letting it open many windows at once makes the migration feel unstable instead of reversible.
- Close-time teardown must stay safe even if Tk widgets have already been destroyed by the window manager.

### Implementation Notes
- `ui_next_qt/config_workspace.py` now merges returned `stats` into stale or missing status snapshots, clears stale preflight state while switching vaults, and surfaces a bold preflight-success notice that points users to `Query -> Activity Log`.
- `ui_next_qt/config_workspace.py` now writes determinate `current/total` progress text onto the Qt progress bar and standardizes the ETA wording to "remaining time".
- `legacy_single_instance.py`, `ui_legacy_tk/app.py`, and `ui_next_qt/main_window.py` now share a file-lock based legacy-window guard plus a short launch debounce so Qt cannot spawn duplicate legacy windows while the second process is still starting.
- `gui.py` now routes task-progress teardown through a widget-existence guard so closing the legacy UI cannot crash on a destroyed `ttk.Progressbar`.

## 2026-03-11 Index Readiness And Watch Throttling Closure

### Decisions
- Query and live watch are no longer allowed to infer index readiness from `stats.chunks > 0`. A vault is queryable/watchable only after a full rebuild writes a persistent completion marker; interrupted rebuilds stay in `pending`.
- Cancelling a rebuild is treated as an explicit incomplete state, not a soft success. The UI must surface `Index pending` and keep watch/query blocked until a resume or a fresh successful rebuild finishes.
- Live watch now has its own persisted hardware-peak control independent from full rebuild. The watcher uses that peak to cap per-batch work and add cooldown so CPU-only incremental sync can be intentionally quieter.
- Preflight success guidance is now an actionable navigation affordance. The start-page notice is clickable and routes directly to the Activity Log instead of auto-jumping tabs.

### Why
- Using chunk counts as a proxy for readiness let cancelled or partially written indexes masquerade as healthy after restart, which polluted both UI status and allowed operations that should have been blocked.
- Watch mode is long-lived background work; its acceptable resource envelope is different from one-off full rebuilds, especially on CPU-heavy machines.
- Users still need a visible success acknowledgement after preflight, but forced tab jumps were disruptive.

### Implementation Notes
- `service.py` now persists full-build readiness in `index_state.json`, reports `index_state/index_ready/query_allowed/watch_allowed` from `status_snapshot()`, clears the marker when starting/discarding incomplete rebuilds, and refuses query/watch when the marker is missing.
- `ui_next_qt/config_workspace.py` and `gui.py` now gate watch start/stop and query banners on explicit `index_state`, surface `pending` as a first-class chip, and keep old snapshots from leaking across vault/config transitions.
- `config.py`, `ui_i18n.py`, `config_workspace.py`, and `gui.py` now expose a dedicated watch hardware-peak setting (5%-90%, default 15%) that persists in config and feeds the watch throttling logic.
- `service.py`, `build_control.py`, and `vector_index.py` now emit rebuild progress more frequently and allow the 90% build profile to run with a less conservative CUDA batch ceiling.

## 2026-03-11 Preflight Navigation And Vector Tail OOM Fix

### Decisions
- The preflight success notice is now a real page-level navigation action. Triggering it must switch the shell back to the Query page and open the Activity Log tab, not merely emit an internal signal.
- Watch start is no longer hidden behind a disabled button when the index is missing. The control stays clickable so the UI can explain why live watch is blocked instead of failing silently.
- Vector rebuild progress is no longer allowed to report completion based on `encoded_count` alone. The visible `current/total` tracks durable write progress, while encoded/written counts are surfaced separately in the detail line.
- LanceDB tail writes are now memory-guarded. The writer may not accumulate multi-thousand-row staged batches before a final flush; write batches are capped by a conservative row ceiling plus a raw-vector byte budget, and the tail explicitly releases Python / CUDA caches before the final drain.

### Why
- Emitting the right signal without actually changing the active tab still feels broken to the user.
- A disabled watch button gave no feedback at the exact moment users needed a reason and next step.
- Large vault crashes were not caused by indexing or parsing loops. The dangerous window was the tail phase after encoding finished, when huge staged LanceDB writes could materialize at once and contend with unreclaimed CUDA cache.

### Implementation Notes
- `ui_next_qt/main_window.py` now routes `showQueryLogRequested` through a shell method that selects the Query page and then opens the log tab.
- `ui_next_qt/config_workspace.py` and `gui.py` now keep the watch button clickable while idle, attach blocked-state copy to the control, and surface an explicit "no index, no watch" message when start is attempted too early.
- `service.py` now emits rebuild-row progress more frequently, and `ui_i18n.py` now exposes encoded/written queue detail so vector tail lag is visible instead of looking like a hang.
- `vector_index.py` now caps effective tail write batches, releases garbage/CUDA cache before the final write drain, and keeps progress honest by separating encoded progress from written progress.

## 2026-03-11 Production Packaging Pipeline

### Decisions
- Production packaging now builds through a committed `OmniClipRAG.spec` plus `build.py`, with the final guarded output restored to `dist/OmniClipRAG/`. The local `dist/OmniClipRAG/runtime` tree remains protected state and the pipeline must never delete or overwrite it.
- The distributable stays `onedir` and `windowed`: `launcher.exe` lives beside an `_internal` payload directory so Windows can start instantly without onefile extraction latency or a console window.
- Packaging is explicit allowlist-based. Only app code, icon resources, and required native/runtime libraries are bundled; model weights, LanceDB data, Hugging Face caches, and `%APPDATA%` runtime state remain external and are regenerated at runtime under the existing AppData paths.
- Windows icon identity is fixed at both packaging and runtime levels so the file icon and Qt taskbar grouping use the same application identity.

### Why
- PyInstaller's default dist cleanup is too dangerous for this repository because `dist/OmniClipRAG/runtime` may hold a preserved local runtime tree. A separate output folder plus path guards removes that blast radius.
- This app ships PySide6, PyTorch, ONNX Runtime, PyArrow, and LanceDB together. Relying on implicit hook coverage alone is fragile, so the spec now explicitly collects the most failure-prone hidden imports and native libraries.
- Keeping the package pure avoids shipping stale indexes or huge downloaded models and guarantees that user state still follows the `%APPDATA%\OmniClip RAG` contract.

### Implementation Notes
- `build.py` now owns safe cleanup, invokes PyInstaller against `OmniClipRAG.spec`, and performs a post-build purity audit that understands the PyInstaller 6 `_internal/resources` layout.
- `OmniClipRAG.spec` now builds `launcher.py` as a `console=False` onedir bundle, adds icon resources, and explicitly gathers PySide6 / shiboken6 / torch / onnxruntime / pyarrow / LanceDB related binaries and metadata.
- `ui_next_qt/app.py` now sets a Windows AppUserModelID before creating `QApplication` and loads the `.ico` plus PNG variants into the shared `QIcon` so packaged desktop surfaces stay visually consistent.
- `scripts/build_exe.ps1` is now a thin wrapper over `build.py` so manual builds and CI-style builds follow the exact same guarded pipeline.

## 2026-03-12 Lean Qt Packaging And External Runtime Split

### Decisions
- Qt is now the only packaged desktop UI. The new shell no longer exposes "Open legacy UI" / "Qt new UI" header controls, and the packaged build no longer ships `ui_legacy_tk`, `gui.py`, or Tk fallback assets.
- The production bundle returned to the historical lean-release strategy: `dist/OmniClipRAG/` contains only `launcher.exe`, `_internal`, `InstallRuntime.ps1`, and `RUNTIME_SETUP.md`; heavy AI/runtime dependencies stay outside the main package and are installed on demand into a sibling `runtime/` directory.
- Vector runtime dependencies are now treated as optional at process start. Missing `lancedb` / `pyarrow` / `torch` / `sentence-transformers` may not prevent the Qt shell from opening; the app degrades to a placeholder vector backend that blocks rebuild/warmup with explicit runtime-install guidance.
- CUDA selection is now aspirational even on a lean build. If the machine has an NVIDIA GPU but the optional runtime is missing, the device picker still exposes `cuda` so the UI can guide the user to install the external runtime instead of hiding the path entirely.

### Why
- The previous full-fat Qt package duplicated the old external `runtime/` strategy and the new all-in-one `_internal` strategy at the same time, inflating the release to multi-gigabyte scale.
- Packaging the legacy Tk surface only increased bundle size and UI clutter after the Qt migration was already stable.
- A lean bundle only works if startup survives missing vector libraries. That required moving the failure boundary from import time to task time.

### Implementation Notes
- `ui_next_qt/main_window.py` and `ui_next_qt/query_workspace.py` dropped the legacy-launch affordance; `app_entry/desktop.py` now allows legacy only in source/dev mode and removes packaged fallback.
- `launcher.py` plus `pyi_rth_omniclip.py` now bootstrap optional `runtime/` and bundled `_internal/.packages` DLL search paths early enough for packaged PySide6 / shiboken startup.
- `vector_index.py` now exposes a `MissingRuntimeVectorIndex`, broadens runtime-install guidance to the full vector stack, and keeps keyword-only query startup alive even when the external runtime is absent.
- `ui_next_qt/config_workspace.py` now prompts runtime installation when users switch to `cuda` on a GPU machine whose lean package still lacks the optional runtime.
- `OmniClipRAG.spec` now excludes the heavy AI stack and unused Qt modules, while `build.py` copies `InstallRuntime.ps1` / `RUNTIME_SETUP.md` into the output and audits the bundle to keep those runtime packages out of `_internal`.


## 2026-03-12 CUDA Guidance Dialog And Device Label Cleanup

### Decisions
- The Qt device picker now separates display labels from persisted config values. Users see `CUDA(N卡GPU)`, but config storage and service/runtime logic still use the stable internal code `cuda`.
- CUDA guidance is now a dedicated Qt dialog instead of a plain `QMessageBox`. Device selection and CUDA-related runtime failures reuse the same structured guidance component so copy, wording, and detection stay consistent.
- Runtime guidance now distinguishes three different layers: system CUDA condition, runtime folder existence, and runtime folder completeness. Users must be told not only whether `runtime/` exists, but whether it is actually complete.
- The top copy on `设置` and `检索强化` was rewritten to explain purpose and tradeoffs directly, instead of forwarding users elsewhere or using vague benefit language.

### Why
- Once the device combo started showing a user-facing Chinese label, binding logic could no longer rely on `currentText()` as if it were the persisted config value. Keeping display and storage separated avoids subtle regressions in save/load and worker config.
- The old runtime popup was technically accurate but too dense and too raw. Users needed a clearer “what happened / why / what to do / current status” structure, plus copyable commands and links.
- A partial `runtime/` folder is materially different from a missing one. Treating both as the same hid the real next step and made repeated installs more likely.

### Implementation Notes
- `ui_next_qt/config_workspace.py` now owns a small device-label mapping layer (`auto/cpu/cuda` <-> localized labels) and always persists `vector_device` from combo item data instead of display text.
- `vector_index.py` now exposes `inspect_runtime_environment()` plus `runtime_guidance_context()`, which centralize adaptive install commands, runtime completeness checks, and the plain-text fallback message used by non-Qt paths.
- `ui_next_qt/runtime_guidance_dialog.py` renders the new themed guidance window with copyable sections for CUDA setup, runtime installation, current status, and CPU fallback guidance.
- `ui_i18n.py` now labels the CUDA option as `CUDA(N卡GPU)`, updates the settings/retrieval top explanations, and keeps shared device-summary copy aligned with the new guidance flow.

## 2026-03-12 Qt Language Switching In The Desktop Shell

### Decisions
- The packaged desktop shell now exposes a persistent language selector in the header, positioned to the left of the header expand/collapse button so it stays visible in the most compact layout.
- Language switching is handled by rebuilding the Qt shell instead of trying to mutate every visible label in place. The app preserves the active tab, query/config view state, splitter state, and current window geometry across the switch.
- Runtime or watch-heavy operations are treated as switch blockers. If a background task or hot watch is active, the language selector must refuse the switch and explain why instead of risking a half-switched UI.

### Why
- The Qt shell is now large enough that in-place relabeling would be brittle and easy to miss. Recreating the shell gives cleaner guarantees that every string comes from the active language bundle.
- Users still expect the switch to feel instant and non-destructive. Snapshot/restore behavior keeps the shell bilingual without forcing them to rebuild context manually.
- The language control needed to remain visible even after the legacy-header actions were removed, so the compact top-right placement became the stable long-term home.

### Implementation Notes
- `ui_next_qt/main_window.py` now owns the header language combo, validates whether switching is currently allowed, snapshots both workspaces, and spawns a replacement `MainWindow` with the new language code before closing the old shell.
- `ui_next_qt/query_workspace.py` and `ui_next_qt/config_workspace.py` now expose snapshot/restore helpers so current results, logs, tabs, form fields, and status banners survive a language change.
- `ui_next_qt/app.py` keeps the replacement window alive through the `QApplication` process lifetime, and config persistence now stores the selected `ui_language` for the next launch.

## 2026-03-12 Runtime Preflight, Build Progress Semantics, And Shared Logging

### Decisions
- 向量运行时缺失不再允许“先把全文建完、最后才在写入向量阶段爆炸”。`service.py` 现在会在模型预热、全量建库、查询和热监听启动前统一做 vector runtime 预检查，缺失时直接抛出同一份安装引导文案。
- Qt 建库进度条的“条形长度”和“数字滚动”现在彻底分口径处理：条形始终跟 `overall_percent` 走，向量阶段的滚动数字优先显示 `encoded_count/total`，同时把 `written_count` 明确并排显示，避免 6% 看起来像 50% 或“卡住不动”的错觉。
- 轻量发布包的 `runtime/` 现在必须通过安装后自校验。`InstallRuntime.ps1` 在落完包后会实际导入 `torch / lancedb / pyarrow / pandas / scipy / sentence-transformers` 等模块，缺项时直接失败，不再让用户带着“看起来有 runtime 目录”但实际不完整的环境继续使用。
- 文件日志现在被视为共享基础设施：日志写入、崩溃栈、Qt worker 异常、服务层关键状态都统一落到 `%APPDATA%/OmniClip RAG/shared/logs`；日志大小上限可配置，并且可在“数据”页直接打开或清理。

### Why
- 实机里的 `12309/12309 -> 写入向量索引 -> CUDA/runtime 引导窗` 说明真正的问题不是“中途随机弹窗”，而是运行时缺失在向量尾段才被发现，导致用户白跑大半程。
- 索引/渲染阶段的 `current/total` 与全局完成度不是一回事。之前 UI 把两个概念混用，暂停/取消时尤其容易让用户误判任务已经过半。
- 轻量包恢复外置 runtime 后，`runtime/` 是否“存在”已经不够，必须校验是否真的满足新 Qt 发行策略所依赖的模块集合。
- 没有稳定的文件日志时，这类长流程尾段问题很难精准回溯；同时日志必须可控，不能无限膨胀，也不能只能手动去 AppData 里找。

### Implementation Notes
- `vector_index.py` 新增 `runtime_dependency_issue()`，按当前向量运行时实际导入必需模块；`service.py` 通过 `_ensure_vector_runtime_ready()` 在 warmup / rebuild / query / watch 前统一调用它。
- `ui_next_qt/config_workspace.py` 新增 UI 侧 ` _ensure_vector_runtime_ready()`，建库、模型预热、热监听启动前都会优先拦截并复用已有 runtime 引导窗；显卡摘要也会把 `runtime_complete=False` 视为“环境仍不完整”。
- `service.py` 把渲染阶段进度发射频率下调到更实时的粒度，`ui_next_qt/config_workspace.py` 则在向量阶段显示“已编码 / 已写入”双计数；`gui.py` 的旧 Tk 进度条语义也已对齐到百分比模式。
- `app_logging.py` 负责滚动文件日志、崩溃日志和异常钩子；`ui_next_qt/app.py`、`service.py`、`query_workspace.py`、`workers.py` 都已经接入，数据页的日志大小设置会即时重配新的文件句柄。
- `scripts/install_runtime.ps1` 现在会在安装后立即用目标 Python 对 `runtime/` 做真实导入校验，避免出现“目录大小看起来像对的，但缺少 lancedb/pyarrow/pandas”这种假完整状态。

## 2026-03-12 Model Bootstrap Decoupling, Device Status Panel, And Reranker Gating

### Decisions
- “开始”页的模型下载入口现在只负责当前选中向量模型的本地存在性检查与下载，不再把 CUDA/runtime 缺失误判成“不能下载模型”。这条链只针对 `vector_model`（默认 `BAAI/bge-m3`）本身。
- Qt `设置 -> 设备` 现在在设备下拉框下方常驻一块可复制的状态面板，明确展示 N 卡检测、CUDA 条件、`runtime/` 是否存在、`runtime/` 是否完整、CPU 模式是否可用，以及当前实际模式（只显示 CPU/GPU，不再暴露 `auto`）。
- 全量建库的进度条语义继续收紧：条形长度始终代表 `overall_percent`，向量阶段的数字口径改为“已写入 / 总数”，`已编码` 仅保留在细节文案里，避免 encoded/written 双头切换造成视觉跳跃。
- 查询阶段对 reranker 又加了一层 service 侧硬闸。即使某个旧实例或测试桩把 `self.reranker` 塞成了可工作的对象，只要 `config.reranker_enabled=False`，查询就绝不会真正调用 reranker。

### Why
- 用户点“下载模型”时，最需要的是把模型缓存补齐，而不是先被迫满足完整向量运行时。下载模型与运行 LanceDB/torch 本来就是两条不同依赖链，必须拆开。
- 轻量发布恢复以后，用户最容易困惑的不是“有没有 CUDA 选项”，而是“当前到底缺哪一层”。把设备/运行时状态拆成多行面板，能显著降低误操作和重复安装。
- 建库体感上最让人焦虑的是条形进度和文本计数互相打架。条形看整体，计数看落盘，这两个维度必须固定职责。
- reranker 开关一旦只靠实例构造时生效，就容易被后续状态同步、测试桩或缓存对象绕过去。service 层再兜底一次，可以把“没勾选却仍然重排”的风险彻底堵死。

### Implementation Notes
- `vector_index.py` 新增 `prepare_local_model_snapshot()`，专门负责当前模型缓存目录准备与 `snapshot_download`；`service.py::bootstrap_model()` 改为直接走这条链，不再调用 `_ensure_vector_runtime_ready()`。
- `ui_next_qt/config_workspace.py` 现在会动态刷新 `下载{model}模型` 按钮文案、`{model}模型已就绪/还没下载` 芯片，以及设备状态面板；模型名变化时会实时同步到这些 UI 元素。
- `ui_i18n.py` 新增/对齐了命名模型芯片、命名下载按钮、设备状态面板相关文案；向量阶段的进度标签也改为只显示 `written/total`。
- `service.py::query()` 现在在调用 reranker 前再次检查 `config.reranker_enabled`；禁用时直接返回 `RerankOutcome(enabled=False, applied=False, skipped_reason='disabled')`。
- 回归测试补到了 Qt 和 service 两侧：覆盖命名模型下载 UI、设备状态面板、向量阶段进度口径、模型下载不依赖完整 runtime、以及 reranker 关闭时的 service 侧硬闸。

## 2026-03-12 Large-Vault Rebuild Hardening For Memory/VRAM Pressure

### Decisions
- 万页级建库在资源压力下现在优先“保守减速”，而不是继续抢资源把系统推到 OOM 边缘。向量阶段新增了前置避让逻辑：只有在写入积压已经形成时，才会因为 RAM/VRAM 接近阈值而主动让行、缩批和短暂停顿。
- LanceDB 写入链现在把“成功落盘”与“从 staged 队列删除”严格绑定。写库阶段如果出现内存/显存压力，会先尝试更小批次重试；只有真正写成功后，当前批次才会从 staged 文档/向量里移除。
- Qt 向量阶段新增了恢复态可视化。即使编码/写入计数暂时不动，界面也会明确显示“正在回收显存/内存”“正在等待写入队列落盘”“正在把已编码内容写入向量索引”，并直接提示“任务仍在继续，请不要关闭程序”。

### Why
- 用户真正担心的不是单次 OOM 本身，而是长达数小时的大库任务在后段才暴露问题，以及卡住时无法判断是“仍在恢复”还是“已经死掉”。
- 旧实现对编码侧 OOM 已经有缩批恢复，但写入侧仍然更脆弱；而且一旦资源压力来自系统里的其它软件，单纯依赖“等 OOM 发生后再缩”会太被动。
- “只要系统内存高就立即让行”会把小任务和轻微波动也拖成假死，所以新的前置避让只在真正有向量写入积压时才生效，避免过度保守。

### Implementation Notes
- `build_control.py` 新增 `note_pressure()` / `in_cooldown()`，让控制器除了 `note_oom()` 之外，也能在高压但尚未硬崩时主动收缩批次并进入短冷静期。
- `vector_index.py` 现在会在向量阶段发出节流心跳：当资源接近阈值、写入队列积压、或末尾正在 flush 时，UI 仍能收到持续进度事件；writer 在遇到内存压力时会按更小写批次重试，并且在重试前清理当前批次可能残留的 chunk 行，尽量避免重复写入风险。
- `ui_i18n.py` 与 `ui_next_qt/config_workspace.py` 增加了 `recovering / backpressure / flushing` 三类向量阶段说明文案，让“数字不动但仍在工作”的状态可见且可理解。
- 回归测试新增覆盖：控制器高压收缩、向量写入阶段的小批次恢复、以及 Qt 恢复态提示；全量测试已覆盖这些路径。
## 2026-03-12 Large-Vault Encode Stall Hardening

### Decisions
- 向量建库现在会先把超长 `rendered_text` 压到安全的嵌入文本长度，再送入 `SentenceTransformer.encode(...)`，并且按“单批总字符预算”动态收紧编码批次，避免极端长块把整个批次拖进异常慢甚至近似卡死的 tokenization / encode 路径。
- 编码阶段新增了后台心跳与慢批次监控。即使某一批向量编码明显变慢，Qt/Tk 也会继续收到 `encoding` 心跳；一旦超过阈值，日志会直接写出该批次的文档数、字符量、截断数量，以及当前线程栈，方便精准定位到底是 `encode` 卡住还是别的底层库阻塞。
- Hugging Face / tokenizers 运行环境现在默认关闭 `TOKENIZERS_PARALLELISM`，减少 Windows 本地长任务里 tokenizer 线程并行带来的不可控抖动。

### Why
- 实测万页库在 40%~50% 的中段“无报错、无推进、界面像死掉”时，`encoded` 与 `written` 只差几十条、写队列也未堆满，更像是下一批 `encode(...)` 被某个异常长片段拖住，而不是 LanceDB 写线程已经完全堵死。
- 大库平均 chunk 很小，但只要混进极端长块，就足以把一个看似正常的小批次拖成分钟级甚至更久；如果没有前置截断和字符预算，这类问题会极难复现且很难从 UI 判断到底是慢还是死。
- 用户已经在本地保守构建上投入数小时，系统需要优先保证“不会静默停住且能留下足够日志”，而不是继续无保护地赌底层库一定会按预期返回。

### Implementation Notes
- `vector_index.py` 新增了 `_prepare_vector_text()`、`_infer_vector_text_char_limit()` 与 `_infer_vector_batch_char_budget()`：嵌入时只对超长文本做 head+tail 安全裁剪，不改 SQLite/LanceDB 里保存的原始 `rendered_text`。
- `LanceDbVectorIndex.rebuild()` 现在不再盲目按件数攒批次，而是同时受“目标批次数量”和“单批总字符预算”约束；一旦遇到超长块，会自动拆成更保守的小批。
- 编码调用外围增加了 watchdog：慢批次会持续发 `encoding` 心跳；超过慢批次阈值会记录 warning；超过更高阈值会把全线程栈打进日志，便于后续精准抓住真实阻塞点。

## 2026-03-13 Large-Vault Rebuild Durability Hardening

- `service.py` 的全量建库状态已经从 `manifest + completed_paths + readable_paths` 的膨胀 JSON 改成紧凑 checkpoint：只保留 `manifest_signature`、阶段游标和少量计数，不再随着文件数线性膨胀到不可恢复。
- 全量建库现在按阶段分别记录 durable cursor：`indexing_cursor`、`rendering_cursor`、`vector_written_count`。恢复时不再依赖巨大路径列表，而是依赖稳定排序后的游标与数据库真相源。
- 渲染阶段的 checkpoint 只会在 `rendered_text + FTS` 批次真正提交后推进，避免断电后出现“状态已前进但数据库还没写进去”的跳过损坏。
- 向量阶段新增了 durable suffix repair：崩溃后会从最后已确认写入位置向前回退一个小窗口，先删掉不确定尾巴，再从该游标继续写入，避免 LanceDB 因最后一批未确认而出现重复/脏尾。
- 全量渲染不再一次性 `fetch_block_lookup()` 把整库 block 映射读进内存，而是改成按需 lazy lookup；查询展示的 `chunk/block lookup` 也同步按需加载，避免大库查询时再次爆内存。
- 启动链新增 `runtime_recovery.py`：如果上次会话异常退出或命中过内存/显存事故，下一次启动会进入 safe startup，先清理进程级向量缓存与 CUDA cache，再延迟 torch/CUDA 探测，降低“上一轮 OOM 后 exe 要重启电脑才恢复”的概率。
- `service.close()` 现在会主动释放进程级向量缓存；`vector_index.py` 也补了 `torch.cuda.ipc_collect()`，避免 Windows + CUDA 在长任务后残留更多显存句柄。

### 下一轮继续加固计划

1. 把 `scan_vault()` 的全量 Path 列表继续下沉成可落盘的 manifest spool，进一步把百万级文件扫描的峰值内存压平。
2. 在 `service.py` 增加阶段感知 watchdog：超过 120 秒无 forward progress 时自动输出诊断包，并区分“编码慢批 / 写入回压 / 真 stall”。
3. 为向量阶段补“已确认 checkpoint 周期”配置，把 durable rewind 窗口和 checkpoint 频率做成可调参数，兼顾极大库恢复时间与安全性。
4. 继续把查询展示链剩余的全量 `fetchall()` 热点替换成按需或分批读取，让百万级状态库在查询侧也维持低峰值内存。

## 2026-03-13 Large-Vault Scan And Watchdog Hardening

- `service.py` 的全量建库文件发现已经进一步改成两遍流式遍历：第一遍只计算 `manifest_signature + total_files`，第二遍再按稳定顺序真正解析，不再把百万级 `Path` 列表长时间常驻内存。
- 稳定顺序不再依赖最终 `sorted(files)` 全量排序，而是对 `os.walk()` 的 `dirnames/filenames` 做局部排序，保证跨重启恢复时 traversal 顺序一致，同时降低超大库下的内存峰值。
- 全量建库新增了阶段感知 watchdog。超过 120 秒无 forward progress 时，它只做安全动作：强制 checkpoint、触发 `gc.collect()`、写 JSON 诊断文件、补一条可见的 UI 提示；不会在不确定 LanceDB / torch 内部状态时粗暴重启活线程，避免二次损坏。
- watchdog 诊断文件落到 `shared/logs/diagnostics/`，会保留最近几份 `rebuild-watchdog-*.json`，其中包含阶段状态、已尝试动作、rebuild state 快照和关键线程栈，后续排查大库卡死会比单纯截图精确很多。
- Qt 新界面在收到 watchdog payload 后会直接在任务详情里显示“已自动执行安全自检并写出诊断文件”，减少用户误以为已经死锁而强关程序的风险。

## 2026-03-13 Versioned Build Outputs And Release Naming

- `build.py` 现在会从 `omniclip_rag.__version__` 读取版本号，并默认把 onedir 构建输出到 `dist/OmniClipRAG-vX.Y.Z/`，而不是复用一个恒定的 `dist/OmniClipRAG/` 目录。
- 新的构建策略只会清理当前目标版本目录里的非 `runtime/` 内容，不会碰旧版本目录；这样历史本地构建可以长期并存，各自的 `runtime/` 也不会被新构建误删。
- `build.py` 现在会同步生成一个 runtime-free 的版本化 zip：`dist/OmniClipRAG-vX.Y.Z-win64.zip`，供 GitHub Releases 直接上传。
- `OmniClipRAG.spec` 的 EXE 名称已经统一改为 `OmniClipRAG`，因此打包后的 Windows 可执行文件和进程名都回到产品名，不再沿用历史遗留的 `launcher.exe`。
- `RUNTIME_SETUP.md` 和 `InstallRuntime.ps1` 已同步改成围绕 `OmniClipRAG.exe` 描述，避免用户在 runtime 安装说明里看到过期的可执行文件名。

## 2026-03-13 Extension Formats Must Stay As An Isolated Subsystem

- 扩展格式已经明确不能作为 Markdown 主链里的几个条件分支去实现，而必须作为平行的隔离子系统推进：UI、状态、索引、热监听、删除/重建都要独立；查询层再统一融合。后续路线再收敛为 `PDF` 专门链 + `Tika` 统一扩展链。
- 这项工作的执行蓝图已固定到 `plans/扩展格式隔离子系统实施计划.md`。后续无论在哪个聊天窗继续实现，必须先读取该文档，再继续写代码。
- 该功能的硬约束是：扩展格式默认关闭时，当前 Markdown 主链的行为必须与“该功能不存在”完全等价；任何破坏该约束的实现都不得合入。


## 2026-03-13 Manual Model Download Split For BAAI/bge-m3

- 当本地缺少 `BAAI/bge-m3` 时，点击“下载BAAI/bge-m3模型”已经固定分成两条路径：`自动下载` 和 `手动下载`。自动下载继续走现有 bootstrap 链；手动下载不再只是给网页地址，而是给出完整的 Windows 终端命令。
- 手动下载命令统一由 `vector_index.py` 生成，避免 Qt / Tk / 后续 CLI 各写一套路径拼接逻辑。命令会根据当前用户真实 `AppData` 目录自动注入 `model_dir` 与 `_hf_home` 的绝对路径，并在命令里先创建目标目录，再执行 `hf download`。
- Qt 新界面已经改成独立的可复制对话框 `model_download_dialog.py`：用户可以分别复制目标目录、CLI 安装命令、官方源命令、镜像源命令，也可以直接打开目标目录与下载页面。
- 旧 Tk 界面维持消息框，但手动下载说明已切到同一份命令生成逻辑，并在弹窗前自动把完整说明复制到剪贴板，减少老界面无法选中文本的历史限制。
- 手动下载提示中明确要求：模型下载完成后需要重启程序，或者再次点击“下载BAAI/bge-m3模型”触发本地完整性校验；不能把模型文件随意多套一层子目录。


## 2026-03-14 Query Workspace Result Counting And Toolbar Simplification

- `Results & Details` 的 Qt 结果表现在新增了最左侧序号列，序号按当前可见顺序实时生成；这意味着排序、页面排序恢复后，用户看到的第 1 / 第 2 / 第 N 条会始终和当前表格顺序一致，不依赖垂直表头。
- `查询台` 顶部行已经收敛为单一主动作 `查询`；`查询并复制` 与 `复制当前上下文` 仍保留底层方法以减少回归风险，但按钮不再出现在主界面，避免高频区域堆叠语义接近的动作。
- 这次改动明确只针对 Qt 主线展示层，不改变查询后端、上下文组装、复制逻辑和旧 Tk 兼容层，确保用户即使完全不关心这次 UI 微调，也不会影响原有检索结果本身。


## 2026-03-14 Extension Format Plan Guardrails Hardening

- `plans/扩展格式隔离子系统实施计划.md` 已补齐几条必须长期生效的硬约束：目录取消勾选前必须二次确认；目录临时失联只能标记源路径异常，绝不能自动清库；每种扩展格式第一版就必须支持“一次扫描变动更新”。
- 扩展格式与 Markdown 主链的关系已从“仅隔离”升级为“隔离 + 统一协调”：任何扩展格式的全量建库、重建、一次扫描变动更新和热监听回压，都必须经过统一任务协调器，保证零干扰主程序。
- 计划文档还明确把“优先使用成熟轻量工具、不自造底层解析轮子”和“未来新增格式必须继续挂在 extensions 子系统下”写成硬规则，避免后续扩展功能把主链重新拖回条件分支式的架构。


## 2026-03-14 Tika-First Extension Strategy

- 扩展格式路线已从“PDF / HTML / MHTML 各自平铺”收敛为两条能力线：`PDF` 走专门解析链，`Tika` 作为统一的多格式外置扩展运行时。
- `Tika` 相关 UI 也已在计划层固定：主界面只暴露 `PDF` 与 `Tika` 两个子页签；`Tika` 通过独立格式选择弹窗管理可用格式，支持搜索、高频置顶、兼容性分层；兼容性差的格式只允许灰色可见，不允许启用。
- 计划同时明确了 `PDF` 不纳入 Tika 白名单、`Tika` 不打进主包、运行时必须外置安装、查询结果必须能区分 `PDF` 与 `Tika` 的具体子格式，避免未来实现重新把扩展子系统写回主链条件分支。

## 2026-03-14 扩展格式阶段 1 记忆

- 扩展格式阶段 1 已从纯占位页签推进到“独立配置 + UI 管理壳层”。
- `omniclip_rag/extensions/registry.py` 现在负责把 PDF / Tika 扩展配置写入独立的 `extensions_registry.json`，路径位于当前工作区 `state/` 下，绝不并入 `AppConfig`。
- `ConfigWorkspace` 新增了扩展格式顶层总览、PDF / Tika 子页签、Tika 运行时状态区和格式选择弹窗，但所有按钮仍只到配置持久化和未接入提示，不触发真实解析/建库。
- 当前工作区目录会作为扩展来源目录中的托管项自动同步进 PDF / Tika 配置；取消勾选任一来源目录必须二次确认，只清除该目录对应的扩展索引语义，不删除原始文件。
- Tika 格式选择窗默认排除 PDF，分为推荐 / 兼容性未知 / 兼容性差三层，其中兼容性差项灰色可见但不可选。

## 2026-03-14 扩展格式阶段 2 记忆

- `omniclip_rag/extensions/runtimes/tika_runtime.py` 现在承载 Tika sidecar 的检测、安装、健康检查与进程生灭；运行时目录固定在 `shared/extensions_runtime/tika/`，不并入 Markdown 主链状态。
- `TikaSidecarManager` 启动时必须通过 `GET /tika` 轮询确认 ready；只有健康检查通过后，UI 才能把状态标成“运行中”。
- Windows 下给 Tika 子进程绑定了 Job Object `kill-on-close`，并在 `MainWindow.closeEvent()` 显式调用 `config_workspace.shutdown_extension_runtimes()`，双保险防孤儿进程占住 9998 端口。
- `ConfigWorkspace` 的 Tika 运行时区已经从占位文案升级成真实检测/安装/启停状态联动，但仍未进入真实业务建库；这一阶段只接 sidecar，不接解析。


## 2026-03-14 扩展格式阶段 3 记忆

- PDF 扩展链已经明确与 Tika 彻底隔离：`parsers/pdf.py` 通过 `pypdf` 逐页抽取文本，`normalizers/pdf.py` 负责空白噪声清理、软换行缝合和有限跨页续句拼接；PDF 不会复用 Tika 的任何解析入口。

## 2026-03-25 扩展超大解析结果分流合同

- 扩展链对“超大解析结果”的判断继续使用输出级阈值，而不是文件大小或扩展名体积猜测：`chunk_count > 5000` 或 `parsed_text_chars > 2_000_000`。
- 但超大后的处理不再一刀切。当前硬分层是：
  - 大文本载体：`pdf / doc / docx / epub / txt / rtf / odt / eml / msg`
  - 结构/噪音风险载体：`html / htm / mhtml / mht / xml / xls / xlsx / ppt / pptx / zip / tar / rar`
  - 其他未明确列出的 Tika 格式默认归入风险桶
- 大文本载体即使超大，也必须保留全文内容：当前做法是不改 parser，只在 parse 之后按原始顺序 regroup，目标块约 `180-260` 中文字、硬上限 `300`；英文目标约 `80-140` 词、硬上限 `180`。这里的“全文保留”指内容不丢，但允许放弃原始过碎 chunk 边界。
- 结构/未知格式一旦超大，直接策略性跳过，不入 store、不入 lexical、不入 vector；这是为了防止网页代码/结构噪音或其他高风险结构化载体污染索引并拖死建库。
- 每次扩展构建都允许写文件级问题日志到 `workspaces/<workspace>/extensions/<pipeline>/logs/issues/<build_id>.jsonl`。当前至少记录：
  - `regrouped_oversized_text_carrier`
  - `skipped_oversized_structured`
  - `error_parse_failed`
  - `error_runtime_failed`
  - `error_io_failed`
- 活动日志只做汇总，不逐文件刷屏；详细路径和 advice 进入问题日志。Why: 这条链要先保证稳定、可诊断、不中毒，后面再单独做命中后的邻接上下文增强。
- `extensions/service.py` 里的 `PdfExtensionService` 已打通 PDF 的预检、全量建库、一次扫描变动更新、删除索引与独立查询，且所有索引都固定落在 `extensions/pdf/` 的独立 SQLite / 向量目录下，不会写进 Markdown 主库。
- PDF 源目录如果临时失联，当前实现会把目录状态标记为 `missing_temporarily` 并拒绝危险的全量重建，而不是把旧索引清空；单个 PDF 文件损坏时只记 warning 并跳过，不会中断整轮任务。
- 主查询层已经接入扩展 broker：PDF 命中会以 `source_family='pdf'` 返回，并强制组装成 `PDF · 文件名 · 第 N 页` 的来源标签，避免跨来源融合后丢失具体 PDF 身份。
- 阶段 3 回归测试新增了 `tests/test_pdf_extension.py`，覆盖 PDF 文本抽取、断句缝合、独立建库与查询页码标识；本轮全量回归结果为 `180/180 OK`。


## 2026-03-14 扩展格式阶段 4 记忆

- `TikaExtensionService` 已经把 Tika 路线从“仅运行时”推进到真实业务链：白名单格式会通过本地 sidecar 返回 XHTML，再由 `normalizers/tika_output.py` 清洗成统一段落结构，最后写入 `extensions/tika/` 的独立 SQLite / 向量库。
- Tika 的一次扫描变动更新已经落地，增量依据仍然是稳定的 `mtime + size` manifest；删除文件时只清理 Tika 自己的索引记录，不会碰 Markdown / PDF 的任何状态。
- Tika 毒文件或解析异常现在只会在扩展日志里记 warning 并跳过，整轮建库和增量任务会继续完成；这条规则与 PDF 链保持一致，避免一个坏文件拖垮整个扩展批次。
- `watch.py` 已新增 PDF / Tika 独立监听服务与持久化 `watch_state`。监听循环使用扩展自己的 manifest 和 pending-change 计数，不复用 Markdown 主链的 watch 状态。
- 当 Markdown 主链正在全量建库，或扩展自身已有重任务占用时，监听触发的扩展增量更新会被 `ExtensionTaskCoordinator` 拦截，只累计到 `watch_state.pending_changes`，从而避免主链与扩展链无序并发导致的资源雪崩。
- 阶段 4 新增了 `tests/test_tika_extension.py`，覆盖 Tika XHTML 规范化、独立建库、一次扫描变动更新，以及监听在主链繁忙时的阻塞/排队行为；本轮全量回归结果为 `185/185 OK`。


## 2026-03-14 扩展格式阶段 5 记忆

- 主查询入口现在已经变成 Broker 结构：Markdown、PDF、Tika 先各自完成来源内召回和排序，再由 `extensions/query.py` 做第二段融合；跨来源阶段明确禁止直接混用不同 LanceDB 表的原始向量距离分数。
- 跨来源默认排序现在采用 family 内排名驱动的 Rank-based Fusion（RRF 风格）并保留极轻量 lexical anchor 仅用于同层细分，目的是避免某一张表因为分布差异而无脑霸榜。
- `SearchHit` 已新增 `source_kind`，并和既有 `source_family / source_label` 一起构成统一身份层：Markdown 标为 `markdown`，PDF 标为 `pdf`，Tika 会标具体子格式如 `docx/html/epub`；Tika 标签统一呈现为 `DOCX(Tika) · 文件名` 这类可见格式名。
- 查询台现在新增了来源复选框 `[Markdown / PDF / 扩展格式(Tika)]`，过滤状态会跟随 Qt 视图快照一起保存和恢复；当所有来源都取消勾选时，UI 会直接阻止查询启动。
- `OmniClipService.query()` 现在允许在 Markdown 主索引未 ready 时继续执行 PDF/Tika 的 extension-only 查询；`status_snapshot()` 也会额外暴露 `query_available_families`，供 UI 或后续状态流做更细的可查询判断。
- 阶段 5 新增回归覆盖 Broker 公平融合、extension-only 过滤、PDF/Tika 来源身份和查询台过滤状态，当前全量测试基线为 `191/191 OK`。

## 2026-03-14 打包版启动串行化记忆

- 发现打包版 `OmniClipRAG.exe` 在启动早期会并发触发两条重型后台探测：
  - `schedule_device_probe()` 在线程中导入 `torch`
  - `schedule_initial_status_load()` 在线程中导入 `lancedb/pyarrow`
- 这两条原生库冷启动在包内并发时，可能导致 Windows 侧致命异常并表现为双击 EXE 无响应或秒退。
- 修复策略：Qt 启动阶段改成严格串行。
  - 先执行 `schedule_startup_background_tasks()`
  - 设备探测完成后，再调度初始状态加载
- Why：避免 `torch` 与 `lancedb/pyarrow` 在打包版冷启动时并发抢占原生初始化路径，优先保证 EXE 能稳定起窗。
- 顺手收敛了扩展 watch 的初始 manifest 竞态：启动 watch 时先同步抓取一次基线 manifest，再进入轮询，避免“监听刚启动就错过第一批新增文件”的时序波动。

## 2026-03-14 打包版 PySide6/Shiboken DLL 搜索路径修复记忆
- 症状：`dist/OmniClipRAG-v0.2.4/OmniClipRAG.exe` 双击无响应，进程立即退出，stderr 指向 `PySide6/__init__.py: Unable to import Shiboken`。
- 根因：PyInstaller 轻量包把 `Shiboken.pyd` 放在 `_internal/shiboken6/`，但 `shiboken6.abi3.dll` 与部分 Qt 依赖 DLL 落在 `_internal/.vendor/PySide6`、`_internal/.vendor/shiboken6`。若启动时未显式把这些 `.vendor` 子目录注册到 DLL 搜索路径，Windows 会在导入 Qt 前直接失败。
- 解决：在 `launcher.py` 与 `pyi_rth_omniclip.py` 同时补齐 `_internal/.vendor/PySide6`、`_internal/.vendor/PySide6/plugins`、`_internal/.vendor/shiboken6` 以及 bundle 根目录对应 `.vendor` 路径的 `PATH + add_dll_directory` 注册。
- 防回归：新增 `tests/test_launcher.py`，锁定 vendored Qt/Shiboken DLL 目录不能再被遗漏。修复后打包版冷启动实测可存活 8 秒以上，不再出现“点了没反应”。

## 2026-03-14 Markdown 查询降级记忆
- 问题：扩展格式未启用、查询台仅勾选 `Markdown` 时，查询仍会先做 `sentence-transformers` / 向量 runtime 检查，导致本来可走 SQLite/FTS 的普通查询被错误拦截并弹出运行环境引导窗。
- 结论：Markdown 查询必须把向量检索视为“增强项”，不是“硬前置条件”。只要主索引已建立，就必须允许退化到纯字面检索继续工作。
- 落地：`OmniClipService.query()` 先执行 Markdown 的存储候选检索，再尝试向量增强；若 `runtime_dependency_issue()` 报错，只记录 warning 并退化为 lexical-only，不再对 `allowed_families={'markdown'}` 抛 `RuntimeDependencyError`。
- 防回归：新增 `tests/test_service.py::test_markdown_query_degrades_to_lexical_when_vector_runtime_is_not_ready`。

## 2026-03-14 扩展格式页收尾记忆
- 来源目录不再只认当前笔记库；扩展页现在会把“当前工作区关联目录 + 已保存笔记库”全部作为托管来源目录显示到 PDF/Tika 两条管线里。
- 这类托管来源目录的同步必须晚于 `extensions_registry.json` 的真实加载，否则会把默认空状态回写覆盖掉已有扩展配置。为此新增了 `_extension_state_loaded` 门闩，只有状态真正载入后才允许根据已保存笔记库列表回写扩展源目录。
- 扩展页顶部全局按钮已经接上真实逻辑：预检和全量建库会按当前子页签分别调用 PDF/Tika 独立服务，不再走“本阶段只接 UI”的 stub 提示。
- 扩展页说明文案已去掉阶段性灰字，避免和当前已落地的真实 PDF/Tika 链路相冲突。


## 2026-03-14 查询相关性与扩展目录表格化记忆
- 跨来源 Broker 排序和“相关性显示”必须彻底分离。`ExtensionQueryBroker.fuse_family_hits()` 现在只负责决定 Markdown / PDF / Tika 的全局顺序，不再把 RRF 融合分覆写回 `SearchHit.score`。Why：不同来源的融合分只适合排序，不适合作为用户看到的“相关性”，否则会出现一堆结果都看起来像 100 分的假象。
- Markdown 查询如果因为主向量 runtime 异常而退化成 lexical-only，查询台现在会在来源筛选下方明确显示提示：这是 Markdown 主向量链的问题，和 Tika 扩展运行时无关；修复本地 runtime 后语义增强会自动恢复。
- 扩展页的来源目录已经开始从“粗粒度勾选列表”收敛为“目录级表格控制台”：每行目录都预留独立的预检、变动更新、建库、删索引入口，并显示自己的状态与进度文本。Why：全局按钮只能做总控，真正的日常使用必须细到目录级，避免用户误以为一次操作会把整条 PDF/Tika 管线全部重跑。


## 2026-03-14 查询降级修复入口记忆
- 查询台里的 `Markdown lexical-only` 提示现在只说明“已降级 + 去配置页修复”，不再提 `Tika`。Why：用户需要的是直接修，不是继续区分技术路线。
- `配置 -> 设置` 的设备运行时区域现在新增了两个固定入口：`自动修复 runtime` 与 `手动修复说明`。自动修复会直接拉起 `InstallRuntime.ps1` 的独立 PowerShell 终端；手动修复复用现有运行时引导窗。
- 这条修复链服务于 Markdown 主向量 runtime，本质上和 Tika sidecar 无关；扩展格式功能只让这个问题从“直接报错”变成了“可降级继续用且能看见修复入口”。

## 2026-03-14 Runtime 管理页与修复入口重构
- `配置 -> Runtime` 现在是主向量 runtime 的唯一管理页；`开始` 页只保留一个轻量状态芯片，不再在 `设置` 页里混放修复按钮。Why：开始页负责让用户知道当前是否完整，真正的下载/清理/刷新管理应该集中到专门页面，避免和设备设置、Tika 扩展、模型下载互相污染。
- Runtime 页按“组件分类”展示，而不是把整个 runtime 当成一坨不可分的黑盒。当前拆成 `vector-core` 与 `vector-store` 两类；每行都有作用说明、就绪状态、缺失项、`修复/下载`、`清理`、`刷新`。Why：这样用户不需要每次全量重下，后续版本如果增减组件也能按类别扩展。
- `scripts/install_runtime.ps1` 现在支持 `-Component all|vector-core|vector-store` 与 `-Source official|mirror`。自动修复和手动修复都必须同时给出官方源与镜像源。Why：轻量主程序默认不内置这些运行组件，修复链必须能在官方源不可用时继续落地，而且不能强迫用户永远整包重装。
- 查询台的 Markdown 降级提示现在带“点击修复”链接，点击后直接切到 `配置 -> Runtime`；提示本身只说明“当前已降级为纯字面检索，修复本地向量 runtime 后语义增强会自动恢复”。Why：用户需要的是一条最短修复路径，而不是继续看到 Tika/扩展路线的概念负担。
- Runtime 页对 `vector-core` 的健康判定不仅看目录是否存在，还会把 `torch` / `sentence-transformers` 的真实导入失败并入状态摘要；这能显式暴露像 `No module named 'http.cookies'` 这种“目录看起来完整，但主向量链其实坏了”的环境。Why：只看文件存在性会产生假完整状态，最终又让查询台莫名降级。

## 2026-03-14 开始页主索引状态去串线记忆
- `开始` 页的索引状态芯片现在只表达 Markdown 主索引，不再把扩展格式（PDF/Tika）的状态概念混进来。Why：主索引和扩展索引是平行子系统，用户在开始页只应该看到“当前笔记库的 Markdown 主索引是否已建立”。
- 当 Qt 还没拿到当前工作区的 `status_snapshot()` 时，索引芯片不再误报“索引还没建立”，而是显示 `索引状态检测中`；查询阻断提示和热监听阻断提示也同步改成“正在检测当前笔记库的索引状态，请稍候”。Why：切换笔记库或启动初期的异步状态加载窗口里，`missing` 会制造假故障感。
- 新增回归覆盖了两件事：未拿到主状态快照时必须显示 `checking`，以及扩展页状态刷新不能改写开始页的 Markdown 索引芯片。本轮全量回归基线提升到 `202/202 OK`。

## 2026-03-14 Runtime 能力细分与 Markdown 主索引回退记忆
- Runtime 页现在按能力模块细分成 `compute-core`、`model-stack`、`vector-store` 三类，而不是继续把主向量 runtime 粗暴地捆成一大块。Why：用户真正需要的是按能力修复，不是每次都整包重下；同时后续版本增加或替换组件时，也能继续沿这三类扩展。
- `scripts/install_runtime.ps1` 已同步支持 `-Component all|compute-core|model-stack|vector-store`。`compute-core` 负责 PyTorch 与计算加速，`model-stack` 负责 sentence-transformers / transformers，`vector-store` 负责 LanceDB / Arrow / 推理存储支撑。Why：这样 Markdown 语义降级时可以优先补模型栈，而不是把整个 runtime 当成黑盒重装。
- `开始` 页的 Markdown 索引状态如果暂时拿不到 `status_snapshot()`，现在会直接回读当前工作区磁盘上的 `state/index_state.json` 与 `rebuild_state.json`，并且只认 Markdown 主索引，不认扩展格式状态。Why：避免 UI 长时间卡在“索引状态检测中”，也避免扩展 PDF/Tika 状态串线到主索引芯片。
- 对来自异步快照的脏 `index_state=checking` 也会优先回落到磁盘真实状态。Why：主链只需要回答“Markdown 主索引是否 ready/pending/missing”，不需要把临时检测态长期暴露给用户。
- 本轮回归把基线提升到 `203/203 OK`，并补了“磁盘已有主索引时索引芯片必须显示 ready”“脏 checking 快照不能覆盖磁盘 ready 状态”两条用例。

## 2026-03-14 Runtime 命令修复与 CPU-only 语义记忆
- `build_runtime_install_command()` 之前会把 `_powershell_literal()` 的返回值再次外包一层单引号，最终生成 `Set-Location -LiteralPath ''D:\...''` 这类无效命令，导致 Runtime 页手动修复复制出来的 PowerShell 命令直接报“LiteralPath 为空字符串”。现已改为只使用一次已转义好的 PowerShell literal，并补回归测试锁定命令格式。Why：手动修复命令必须做到复制即用，不能让用户自己再猜怎么改引号。
- Runtime 自动修复链继续保持“只拉起 PowerShell，不在主进程里执行下载”，并通过 Qt 单测验证 `subprocess.Popen(..., cwd=<app_dir>)` 的实际入参；另外补了一条不下载任何东西的真实 shell 冒烟，确认构建目录下 `InstallRuntime.ps1` 的定位与 `Set-Location` 均正确。Why：自动修复是否真正下载是用户决策，但拉起路径和工作目录必须先验证到位。
- Runtime 页现在在 `compute-core / model-stack / vector-store` 之外新增 `gpu-acceleration` 可选类别；当本机没有可用于本程序的 NVIDIA GPU 时，这一类会显示为 `当前无需安装`，不会再把 CPU-only 机器误导成“Runtime 不完整”。开始页的新手指引芯片会显示 `非N卡都已完整`，Runtime 页摘要也会明确写出“GPU 加速这一项不需要安装”。Why：CPU-only 是合法完整状态，不应该被 GPU 路线绑架。
- `gpu-acceleration` 行的修复入口会映射到 `compute-core + cuda` 安装目标；若当前机器没有 N 卡，则修复按钮与清理按钮都不再鼓励用户操作，并直接提示“无需安装/无需清理”。Why：Runtime 页应该按能力模块引导用户，而不是让没有 GPU 的机器反复下载毫无意义的 CUDA 组件。

## 2026-03-14 Runtime 挂起更新与热修复记忆
- 运行中的 OmniClipRAG 会锁住 `runtime/` 里的 `.pyd/.dll`，直接在 live runtime 上执行 `pip --target` 会触发 `WinError 5`。这不是单个包的问题，而是 Windows 文件锁和当前进程已加载模块的物理限制。
- `scripts/install_runtime.ps1` 现在不再尝试覆盖 live runtime，而是统一下载到 `runtime/.pending/<component>/payload`，并写出 manifest。Why：这样手动修复和自动修复都可以在程序保持打开时完成下载，不会再因为 live `.pyd` 被占用而失败。
- `launcher.py` 在启动早期会先扫描 `.pending` manifest，并在 bootstrap 本地包目录之前把待应用 payload 覆盖到 live runtime。Why：只有在新进程尚未加载这些扩展模块之前，Windows 才允许安全替换 `.pyd/.dll`。
- Runtime UI 说明、手动命令和自动修复按钮都已经同步到“下载可在程序打开时进行，重启后自动应用”的语义；自动修复会显式传入 `-WaitForProcessName OmniClipRAG`，手动命令也同样带上这个参数。Why：用户不应该再被迫先手动关程序，也不应该再看到旧的 in-place 安装命令。
- Runtime 页摘要会额外显示待应用更新的组件列表，提示“保持程序打开也没关系；下次启动会自动切换”。Why：下载完成但尚未重启时，系统需要明确告诉用户当前是 pending 状态，而不是“好像还是没修好”。
- Windows PowerShell 5.1 不能使用 `[System.Text.Json.JsonSerializer]`，所以 `InstallRuntime.ps1` 里的 pending manifest 序列化必须改回 `ConvertTo-Json -Compress`。Why：构建版默认仍可能由 `powershell.exe` 启动，脚本不能只在 PowerShell 7 下可用。
- Runtime 页的组件表格在首次进入时会因为 cell widget 的 size hint 还没稳定而显得偏松；`_populate_runtime_component_table()` 现在会在本轮布局结束后再做一次 `singleShot(0)` 的二次收口。Why：默认视觉就应该和点击“刷新”后的紧凑状态一致，不能要求用户先手动刷新一次。
- `InstallRuntime.ps1` 不能再把模块清单 JSON 直接作为命令行参数传给 Python 校验脚本。PowerShell 会吃掉 JSON 里的双引号，Python 最终会拿到 `[torch,numpy,...]` 这种非法 JSON；现在统一改成先把 `required-modules.json` 写到 pending 目录，再让 Python 从文件读取，同时 `required-modules.json` 与 `manifest.json` 都使用无 BOM UTF-8 写入，避免 Windows PowerShell 5.1 下再次触发 `JSONDecodeError` 或 manifest 读失败。

## 2026-03-15 Runtime 粒度收口记忆
- Runtime UI 不再把主语义链拆成 `compute-core` 与 `model-stack` 两行给用户分别修复；实际使用上它们共同构成一个“本地语义核心”，拆开只会造成“修完一个仍提示另一个坏了”的割裂体验。现在 UI 收口成三类：`semantic-core`、`vector-store`、`gpu-acceleration`。Why：用户理解的是能力，不是 Python 依赖分层。
- `scripts/install_runtime.ps1` 继续兼容旧的 `compute-core/model-stack` 参数，但新 UI 与新手动命令统一使用 `-Component semantic-core`。`semantic-core` 会一次下载 PyTorch + sentence-transformers + transformers 这一整条主语义链，避免主查询修复时还要来回猜到底补哪半边。
- Runtime 页在组件分类说明下新增灰色加粗提示“请勿同时下载同一组件”。Why：不同组件可以并行，但同一组件如果开多个终端同时写同一个 `.pending/<component>` 目录，会互相覆盖，用户侧必须明确知道这条硬限制。


## 2026-03-15 Runtime 误判与启动提示修复记忆
- 打包版 runtime 即使文件已经下载完整，UI 仍可能误判“需要修复”；根因不是下载源，而是健康探测时没有先把 runtime 目录临时挂进 import / DLL 搜索路径，导致 `sentence-transformers` 在探测阶段误报失败。
- 修复策略：`vector_index.detect_acceleration()` 和 `runtime_dependency_issue()` 统一走 `_runtime_import_environment()`，在探测期间临时挂载 `runtime`、`.libs` 和 `torch/lib`，并读取 `_runtime_bootstrap.json` 中的 DLL 目录；退出后恢复现场，避免污染主进程。
- 打包启动链还要避免把外部 Python 的 `stdlib/platstdlib` 注入 frozen 进程，否则会把 `C:\Python313\Lib` 这类系统标准库混进来，诱发 `asyncio/base_events` 之类的假性崩溃。`launcher.py` 现在只接收 runtime 的 DLL 目录，不再接收外部 stdlib 路径。
- `InstallRuntime.ps1` 不能再把模块清单 JSON 直接经由 PowerShell 命令行传给 Python；PowerShell 5.1 对 JSON / 引号 / BOM 的兼容性太差，最终会出现下载成功但校验失败的红字。现在改成把待校验模块写入 `required-modules.txt`，Python 逐行读取。
- 双击 EXE 后没有反馈会导致用户重复点击。Qt 启动链现在增加了一个可移动、可最小化的 `StartupProgressDialog`，在主窗口真正显示前持续给出“正在启动”的可视反馈。

- 2026-03-15 runtime 修复链补充：pending staging 只能作为“已下载待应用”提示来源，绝不能参与 live runtime 健康检测或导入路径拼装；否则会把 `runtime\.pending\...\payload` 里的半安装组件当成正式运行时，造成 torch DLL / sentence-transformers 的假性红灯。对应修复在 `vector_index.py`，live probe 现在只看正式 `runtime/`。同时 `launcher.py` 必须在启动最早期先执行 `apply_pending_runtime_updates(runtime_dir)`，否则用户下载成功后重启仍不会生效。Runtime 页的“刷新检测”也已改为后台 worker 触发，避免把重量级加速探测放在 UI 线程里造成卡死。

- 2026-03-15: The legacy Tk desktop source has been permanently removed from the repository (`omniclip_rag/gui.py`, `ui_legacy_tk/`, `legacy_single_instance.py`, `ui_tooltip.py`, and `tests/test_gui.py`). Qt is now the only supported desktop UI in both source and packaged builds.

- 2026-03-15 runtime 稳定性补充：`vector_index._runtime_import_environment()` 必须忽略 `_runtime_bootstrap.json` 里的 `stdlib/platstdlib`，只把 live `runtime/` 根目录和必要 DLL 目录挂进当前探测环境。Why：安装 runtime 所用的系统 Python 标准库一旦被注回 frozen 进程，会直接污染 `_multiprocessing` / `asyncio.base_events` / `huggingface_hub` 等导入，表现为‘下载成功但仍红灯’的假故障。
- 2026-03-15 启动体验补充：`omniclip_rag.ui_next_qt.app` 不能在模块顶层直接导入 Qt 并定义启动窗类，必须改成懒导入/懒建类。Why：否则双击 EXE 后要先等完整 `PySide6` 导入结束，启动提示窗会和主窗口几乎同时出现，用户会误以为程序没响应而重复双击。


## 2026-03-15 Runtime 组件切换改造记忆
- `runtime` 的反复“下载成功但仍显示需要修复”，根因不是单一包缺失，而是旧方案一直在把新包下载到 `.pending` 后再尝试覆盖 live `runtime/` 根目录；一旦提升过程被打断或混用旧脚本，live 根目录就会变成半旧半新的污染状态。Why：这种根目录原地覆盖在 Windows 上天然容易被 `.pyd/.dll` 占用和部分目录残留击穿。
- 修复方向已切换为“组件独立目录 + 活动组件注册表”：`semantic-core` / `vector-store` 后续应安装到 `runtime/components/<component>-<version>` 这类隔离目录，通过 `runtime/_runtime_components.json` 指向当前活动版本；运行时探测只认活动目录，不再依赖把整个 live 根目录抹来抹去。Why：这样就算程序开着下载，也是在写全新的组件目录，不会再把当前正在使用的运行时写坏。
- `runtime_layout.py` 现在优先从 `_runtime_components.json` 解析活动组件根目录；只有没有注册表时，才回落到旧的 `runtime/components/<component>` 或历史 flat `runtime/` 布局。Why：新旧构建必须能平滑过渡，不能因为切换布局把历史包直接判死。
- `_runtime_bootstrap.json` 中来自安装器 Python 的 `stdlib/platstdlib/dll_dir` 都不能再被 frozen 进程直接采信；特别是外部 `C:\Python313\Lib` 一旦回灌，会再次污染 `_multiprocessing` / `asyncio` / `huggingface_hub` 等导入。现在探测只接受位于 runtime 根目录内部的 DLL 路径。Why：安装器环境和应用运行环境必须彻底隔离。
- `semantic-core` 的用户语义已经固定为“CPU 基线语义核心”，即使本机存在 N 卡，也不能在修这条主链时默认拉 CUDA 版 torch；GPU 加速行是后续可选升级，而不是高级搜索的前置条件。Why：CPU 语义检索必须是默认可恢复能力，否则用户会被 GPU 依赖绑架而长时间无法恢复正常搜索。
- 2026-03-15：修复启动链两个高频坑。其一，Windows 上 `%APPDATA%\OmniClip RAG\shared` 已存在时，`pathlib.Path.mkdir(exist_ok=True)` 仍可能在 WinError 183 后因 `is_dir()/stat()` 触发 WinError 5；现在 `config.py` 统一通过 `_ensure_directory()` + WinAPI `GetFileAttributesW` 兜底，已存在目录不再被误判成权限错误。其二，`--selfcheck-query` 诊断入口与正常桌面启动必须硬隔离；`app_entry/desktop.py` 现在只有在明确给出诊断参数（或开发者显式设置 `OMNICLIP_ALLOW_SELFCHECK=1`）时才进入自检模式，避免构建版被误带入自检路径。自检 JSON 输出也统一支持 `Path -> str` 序列化，防止再次因 `WindowsPath` 序列化失败而误判为算法链路损坏。

## 2026-03-16 Markdown 主查询 RCA 收束计划
- 当前最关键的未闭环问题，不再定义为“Runtime 仍然有点红”，而是定义为：构建版 EXE 中 Markdown 主查询看起来没有真正执行高级语义召回。Why：能力健康、Runtime UI 变绿、脚本下载成功，都不能证明这次 query 真正跑了 vector retrieval。
- 后续排错已收束到 `plans/Markdown主查询与Runtime稳定性RCA计划.md`。任何新窗口或新 AI 接手时，必须先读这份计划，再继续推进；禁止再围绕 Tika、PDF、启动动画、样式等外围问题分散注意力。
- 查询排错日志现在可通过 `配置 -> 数据 -> 把详细查询排错日志写入活动日志` 开关控制是否写入普通活动日志；查询台仍可显示 trace。Why：这让后续 RCA 能在不污染普通日志的情况下，按需记录 `查询预期 / 查询实际 / 查询诊断` 对照。
- 这条战线的唯一正确推进顺序已经固定为：
  1. 结构化查询日志（QUERY_PLAN / QUERY_FINGERPRINT / QUERY_STAGE）
  2. lexical-only / vector-only / hybrid 三模式对照
  3. 证明健康探测与真实查询是否使用同一 RuntimeContext
  4. 再修真正根因
  5. 最后补端到端 canary
  任何偏离这条顺序的“继续试着修一圈”都视为无效推进。

- 2026-03-16 Markdown 主查询 RCA 计划已按更高阶审核意见升级：Phase 1 现在必须记录 build/runtime/index 漂移指纹与决策级 QUERY_STAGE，Phase 2 从三模式升级为四模式（`lexical-only / vector-only / hybrid_no_rerank / hybrid`），且 `vector-only` 被明确禁止走独立 helper，必须复用同一 QueryService / RuntimeContext / workspace/index 主链。Why：当前问题的核心不是“能 import”，而是“这次 query 是否真的执行了语义召回，以及哪一步先归零”。
- 2026-03-16 RCA Phase 1 已实际落地：`service.py` 现在会为每次 Markdown 查询生成 `QUERY_PLAN / QUERY_FINGERPRINT / QUERY_STAGE` 三组结构化 payload，并仍通过现有 `trace_lines` 进入查询台和活动日志；`app_entry/desktop.py --selfcheck-query` 也已支持 `--query-mode <lexical-only|vector-only|hybrid_no_rerank|hybrid|suite>`，用于后续四模式对照，且复用同一个 QueryService 主链。
- 2026-03-16 RCA 进展补充：Phase 2 的四模式自检已经在最小临时工作区和真实工作区的源码入口上跑通。真实工作区固定 query `我的思维` 当前表现为：`lexical-only` 候选数为 0，而 `vector-only / hybrid_no_rerank / hybrid` 都是 `vector_query_planned=true` 但 `vector_query_executed=false`，并落到 `fallback_reason=vector_runtime_unavailable`。Why：这再次证明当前必须先区分 capability healthy、runtime root 对齐和 query-time vector 实际执行，而不能再把 Runtime UI 状态当成查询已经用上高级搜索的证据。

## 2026-03-16 Markdown 主查询 RCA 关键突破
- 真实用户工作区 `<user-logseq-vault>` 上的固定 query `我的思维` 已经通过四模式主链对照证明：问题不再是“Runtime 看起来没装好”，而是 query-time vector adapter 真正执行时的两处错误。
- 第一处错误是 runtime 导入优先级：`semantic-core` 组件导入时如果同时存在 legacy flat `runtime/` 根目录，会被根目录里残留的坏 `transformers` 抢占，导致健康探测和真实查询看到不同模块源。修复方式是：`_runtime_import_environment()` 与 `_probe_runtime_semantic_core()` 统一让 `semantic-core` 自动携带 `vector-store` 依赖，并保证 `semantic-core` 的路径优先级高于 flat runtime。
- 第二处错误是 LanceDB 查询输入类型：`LanceDbVectorIndex.search()` 之前会把 `_encode()` 返回的 `numpy.ndarray` 直接传给 `table.search(...)`，在真实运行时触发 `TypeError: Unsupported query type: <class 'numpy.ndarray'>`。修复方式是 query-time 与写入时保持一致，在 `search()` 前先调用 `_coerce_vector()` 转成 `list[float]`。
- 修复后，同一条真实主链已经验证：
  - `vector-only` 能返回 30 条命中；
  - `hybrid_no_rerank` 能返回 30 条命中；
  - `hybrid` 能返回 30 条命中且 `reranker_applied=true`。
- 当前剩余问题已经收缩成：中文 query `我的思维` 的 lexical-only 为 0，这属于 FTS / tokenizer / query normalization 的单独分析课题，不再和 Runtime 健康或语义链执行混为一谈。
## 2026-03-16 Markdown 主查询 RCA 最终收口
- 继续对真实工作区和构建版 RuntimeContext 做零下载 suite 自检后，最终钉死了更深一层的结构根因：**并不是 Runtime 仍然坏，而是 `LanceDbVectorIndex` 过早拉起 vector-store 栈 + `SentenceTransformer(...)` 模型实例化发生在 runtime context 外。**
- 第一层根因：`LanceDbVectorIndex.__init__()` 和状态探测过早导入/连接 `lancedb`，把 `pyarrow / pandas / numpy` 这条 vector-store 栈提前注入进程。Why：这会让真正的语义模型导入阶段与运行时探测阶段共享一份已经被 vector-store 污染过的解释器状态，随后非常容易触发 `cannot load module more than once per process` 一类的假性依赖错误。
- 第二层根因：`_default_embedder_factory()` 之前只把 `from sentence_transformers import SentenceTransformer` 放进 `_runtime_import_environment(component_id='semantic-core')`，但真正的 `SentenceTransformer(...)` 实例化与本地模型权重加载发生在 context 外。Why：导入成功不等于模型构造成功；权重加载阶段同样依赖完整的 runtime `sys.path`、DLL 搜索路径和组件目录。
- 对应结构修复已经固定为两条硬规则：
  1. `LanceDbVectorIndex` 必须保持 **lazy vector-store bootstrap**，初始化和状态读取不能提前连接 LanceDB；只有真正打开/创建表时才允许导入 `lancedb`。
  2. `SentenceTransformer(...)` 的**完整实例化**必须包在 `_runtime_import_environment(component_id='semantic-core')` 里，不能只包 import 语句。
- 修复后的真实工作区 suite 自检结果已经明确证明 Markdown 主查询主链恢复：
  - `lexical-only`：`result_count=0`
  - `vector-only`：`result_count=30`，`vector_candidates_raw=300`
  - `hybrid_no_rerank`：`result_count=30`
  - `hybrid`：`result_count=30`，`reranker_applied=true`
- 这意味着当前构建版主链的真正剩余问题已经缩小到**中文 lexical/FTS 命中策略**，而不是 Runtime 安装、健康探测或 query-time vector 执行本身。

## 2026-03-16 构建版 suite 复验记忆
- 已直接对验收构建物 `dist/OmniClipRAG-v0.2.4/OmniClipRAG.exe` 执行 `--selfcheck-query --query-mode suite`，目标工作区固定为 `<user-logseq-vault>`，query 固定为 `我的思维`，阈值 `0`，条数 `30`。

## 2026-03-23 v0.4.3 发布链硬化与公开仓库净化
- `v0.4.3` 这轮不是做新子系统，而是把最近积累的热修全部收成一条可以公开发布的稳定版本线：语义后端状态必须诚实、模型与 reranker 下载必须严格跟随当前 active data root、MCP 的降级语义必须和桌面端一致。
- 发布链层面重新补回了缺失的 `OmniClipRAG-MCP.spec`。Why：`build.py` 已经把 `GUI ZIP + MCP ZIP + MCPB` 视为同一条正式发布链，如果根仓库缺这个 spec，MCP EXE 和 `.mcpb` 的重建能力只是“看起来支持”，不是可复现事实。
- 对公开仓库做了一轮路径净化：追踪文档中涉及真实用户绝对路径的记录改写成 `<user-logseq-vault>`、`%USERPROFILE%\\Downloads\\sample-tika-corpus` 这类占位路径。Why：架构记忆应该保留“问题类型和决策”，不应该把真实个人目录当成永久公开元数据。
- 本次复验不再依赖源码态入口，实际产物写出到 `.tmp_dist_runtime_diag_packaged_suite.json`。Why：以后讨论“构建版到底有没有真的跑高级搜索”，必须先看 frozen EXE 自己的 suite 证据，而不是混用源码态结果。
- 复验结果再次确认：
  - `lexical-only=0`
  - `vector-only=30`
  - `hybrid_no_rerank=30`
  - `hybrid=30`
  - `reranker_applied=true`
- 这条记录的意义是：Markdown 主查询的 CPU 语义召回 + reranker 现在已经在**真实构建版**上被钉死为可执行状态；如果后续 GUI 点查再次表现异常，优先怀疑 QueryWorker/UI 参数传递、查询模式切换或显示链，而不是重新回头怀疑 Runtime 下载器本身。

## 2026-03-16 GUI 查询链 live snapshot 记忆
- `QueryWorkspace._validate_query_request()` 之前默认只读取自身缓存的 `self._config / self._paths`。Why：这在“用户已在配置页切换笔记库，但尚未触发完整保存/回灌”的窗口里，会让查询页继续拿旧 workspace 去搜，从而出现“构建版 selfcheck 绿、GUI 点同一 query 却像没走高级搜索”的分裂现象。
- 现在查询页不再信任自己的缓存配置，而是支持 `runtime_snapshot_provider`。每次点击查询前，都会优先向配置页实时拉取一份当前 live snapshot。Why：查询必须基于用户眼前这套 workspace/data-root/runtime 组合，而不是基于某次历史同步留下的快照。
- `ConfigWorkspace` 新增 `current_runtime_snapshot()`，`MainWindow` 在组装 Query/Config 两页后会显式把 provider 接给查询页。Why：不再依赖 `runtimeConfigChanged` 这种“配置发生变化后希望它能及时广播”的被动同步链，而是把查询前 snapshot 拉取变成主动动作。
- 新增 Qt 回归验证：当 QueryWorkspace 本地缓存的是旧 vault、provider 返回的是新 vault 时，`_validate_query_request()` 必须优先采用 provider 返回的新 vault/new paths。Why：这条用例直接锁死本轮 RCA 里最可疑的 GUI 偏差点，防止以后回归到“查询页拿旧笔记库”的状态。

## 2026-03-16 v0.3.0 源码里程碑记忆
- `v0.3.0` 选择以源码里程碑形式发布，不附带 EXE 资产。Why：扩展格式子系统与 Runtime/Markdown 查询链已经大规模并入主线，但打包体验仍在继续收尾，先发源码与文档更诚实。
- 这次版本正式把三条长期并行的主线合并入仓库：`extensions/` 扩展格式隔离子系统、组件化 Runtime 管理链、以及 Qt-only 桌面主链。Why：后续所有桌面修复都必须围绕这三条真实主线，不再回到历史分叉。
- 旧 Tk UI 代码已彻底从仓库删除，未来只维护 Qt 桌面链。Why：双套桌面实现已经被证明会放大行为漂移与排错成本，继续保留没有工程价值。
- 仓库新增最小公开 `笔记样本/` 合成样本库，用于 parser / preflight / service / Qt UI 回归，不包含个人数据。Why：之前测试大量依赖本地私有样本，导致源码发布前无法在干净环境中稳定自测。
- 这次发布前的回归采用“分组全绿”策略，而不是依赖一条超长 discover 命令。已验证通过的分组包括：扩展链、parser/preflight、service/vector、Qt UI、runtime/launcher/desktop、自检脚本与纯后端工具链。Why：当前环境下单条超长全量命令容易超时，分组结果更可审计。 

## 2026-03-16 GPU Runtime 与扩展建库 UX 新阶段
- 当前主战线已经从“CPU 主查询是否真的执行高级搜索”进入第二阶段：`GPU Runtime 真实生效 + 扩展来源目录建库 UX 收尾`。Why：CPU 语义主链已明显收口，但 GPU Runtime 仍把“组件安装、能力探测、执行验证”混成一个派生灯，扩展来源目录建库也仍偏数值化，用户无法稳定判断系统到底是否在工作。
- 新阶段主计划固定为 [plans/GPU Runtime与扩展建库UX收尾计划.md](/D:/软件编写/OmniClip%20RAG/plans/GPU%20Runtime%E4%B8%8E%E6%89%A9%E5%B1%95%E5%BB%BA%E5%BA%93UX%E6%94%B6%E5%B0%BE%E8%AE%A1%E5%88%92.md)。后续任何窗口或新 AI 接手时，必须先读这份计划，再决定是否继续改 GPU Runtime、Runtime 刷新检测或扩展建库 UX。
- 当前已确认三条关键事实：
  1. `详细查询排错日志写入活动日志` 仍走 `RotatingFileHandler(maxBytes, backupCount)`，受同一套单文件大小与轮转上限限制，不会无限膨胀。
  2. `gpu-acceleration` 当前在 UI 中是派生能力行，不是真正独立组件；安装目标映射到 `semantic-core + cuda`，但 ready 判定又额外依赖 `detect_acceleration().cuda_available`，导致“下载成功”与“UI 转绿”之间没有稳定一一对应。
  3. 扩展来源目录当前主要展示 `overall_percent/current/total`，阶段感不足；已建成目录再次点击建库时，也缺少“扫描更新 / 重建”的明确分流。
- 2026-03-16 补充：GPU Runtime 新阶段已正式吸收高级 AI 的 6 个硬约束：漂移指纹、决策级日志、四模式对照、`vector-only` 同主链、分层探测、零下载 canary。Why：这条线以后不再允许靠“UI 看起来绿了”或“`torch.cuda.is_available()` 为真”来宣布 GPU 真正可用。
- 2026-03-16 当前代码已经开始按新计划执行：`gpu-acceleration` 行内部状态已拆出 `install_state / probe_state / execution_state / execution_verified`，并新增 Qt 回归锁定“未做执行验证不能转绿、只有执行验证通过后才能转绿”。Why：先把 GPU 行从单一派生灯收口为可证明的三层状态，后面才能继续做运行时探测和查询设备验证。
- 2026-03-16 补充：查询侧现在也有针对 GPU 执行证据的回归，要求 `runtime_warnings` 和 `QUERY_STAGE` 在向量召回 / reranker 真跑到 `cuda:0` 时留下 `markdown_vector_cuda_ready`、`markdown_reranker_cuda_ready`、`vector_actual_device`、`reranker_actual_device`。Why：这让“UI 绿了但日志证明不了”不再成为灰区。
- 2026-03-16 还确认了一条重要边界：源码态仓库根目录 `runtime/` 当前并不完整，因此源码态 `detect_acceleration()/refresh_runtime_capability_snapshot()` 的结果不能再被当成构建版 GPU 是否就绪的结论。Why：GPU 这条线以后必须坚持“构建版优先、源码态只做开发回归”的验收规则。

## 2026-03-16 GPU Runtime 刷新/验证拆分与扩展建库进度收口
- `Runtime` 页面现在正式拆成两条不同链路：
  - `刷新检测` 走 `runtime_management_snapshot(verify_gpu=False)`，只做 live runtime 轻/中探测，并复用缓存的 GPU 执行状态。
  - `执行验证` 只出现在 `gpu-acceleration` 行，走 `runtime_management_snapshot(verify_gpu=True)`，显式触发零下载 GPU smoke。
  Why：不能再让“刷新 UI 状态”和“证明 GPU 真能执行”混成一个动作，否则用户会看到下载成功但仍红、点击刷新又像卡住。
- `ConfigWorkspace._refresh_runtime_management_ui()` 现在会复用**同一份** RuntimeContext snapshot；`_runtime_component_state()` 与 `_populate_runtime_component_table()` 已支持传入 context。Why：之前一次刷新里会重复重算多次 live runtime 状态，导致重量级探测被放大并放大 UI 卡顿感。
- `gpu-acceleration` 行目前继续保留为短期 capability row，但内部状态已经固定拆成：
  - `install_state`
  - `probe_state`
  - `execution_state`
  - `execution_verified`
  只有执行验证通过后才允许转绿。Why：这让“已安装”“探测通过”“真正执行过”不再被一个派生布尔值混掉。
- 扩展来源目录行现在已经具备“已建索引 -> 先选 `扫描更新 / 重建`”的明确交互；行级进度同时显示阶段、当前文件、处理/跳过/错误/删除统计和关闭风险提示。Why：用户需要判断“它现在在干什么、能不能关”，而不只是看一个百分比条。
- 扩展来源目录在空闲态会显示已索引摘要（文件数 / chunk 数 / 向量文档数）；这样即使当前没有任务，用户也能看到这行目录已经有历史索引结果。Why：避免来源目录表在任务结束后重新退回成“像什么都没做过”的状态。

## 2026-03-20 数据目录 GUI 受限模式
- 当前环境根 `active_data_root` 不可用时，GUI 不再走“阻断弹框循环”，而是进入 **主窗口受限模式**。Why：用户必须保留自救入口，但又不能让程序带着假环境继续跑。
- 受限模式只保留 `开始 / 配置` 视图；`QueryWorkspace` 不构建，查询/运行时/工作区相关能力都不启动。Why：这不是 fallback 到空环境，也不是第二套恢复系统，而是最小修复壳。
- 恢复入口固定在 `ConfigWorkspace` 的开始页顶部卡片，显示失效路径与具体原因，并只提供：
  - `重试`
  - `选择新的数据目录`
  - `退出`
  Why：目录不可用时，用户需要明确修复入口，但不应误以为其它功能仍能正常工作。
- 恢复态里的数据目录切换仍沿用现有环境切换契约：**预检 -> 确认 -> 写 bootstrap -> 受控重启**。Why：环境切换继续坚持“重启切换，不做热迁移”这条低风险路线。
- MCP / headless 保持原有策略：目录不可用时**明确失败退出**，不做 fallback。Why：只有 GUI 有可视化修复入口，非 GUI 入口必须继续坚持单根真相与可证明失败。
- 为手测和回归增加了仓库内样本目录根：`.tmp/data_root_recovery_samples/`。Why：后续验证恢复态、切换到旧环境、新环境、坏路径时，不再依赖真实 `%APPDATA%` 环境。

## 2026-03-20 数据目录恢复体验收口
- `probe_data_root()` 现在必须把目录语义稳定分成五类：`existing / new / invalid_not_directory / invalid_not_environment / invalid_broken_environment`。Why：GUI 恢复态不能再把“空目录新环境”“普通非 OmniClip 目录”“损坏环境”都视觉上折叠成同一种“4 项未就绪”状态。
- 判定规则已经收紧：只有**真正空目录**才允许落到 `new`；非空目录若没有完整环境证据，一律不能伪装成新环境；只要检测到部分 OmniClip 痕迹但不完整，就归到 `invalid_broken_environment`。Why：这避免用户误把损坏环境当成干净新环境继续使用。
- GUI 不再向用户暴露底层 reason code，例如 `not-a-directory`、`not-an-omniclip-environment`。现在统一通过 `ui_i18n.data_root_reason_text()` / `data_root_probe_summary_text()` 映射成人类可读文案。Why：内部码适合调试，不适合环境切换产品化界面。
- `ConfigWorkspace` 现在会在恢复态与待切换态里明确区分：
  - 当前坏掉的 `active_data_root`
  - 用户当前选择的目标目录
  - 该目标是现有环境 / 新环境 / 非环境目录 / 损坏环境
  Why：恢复壳的职责是帮助用户修目录，而不是让用户自己猜“为什么现在看起来像个空环境”。
- “新手指引”下面那 4 条 readiness checklist 继续只表达**环境已成立后的工作准备状态**，不再承担“目录是否有效”的职责。Why：环境有效性与工作就绪度是两件不同的事，混在一起会误导用户。
- `数据目录（可多选\切换）` 现在支持**移除已保存路径**，但移除动作只会从 `known_data_roots` 里忘记这条路径，不会删除磁盘目录；当前激活的数据目录不可移除。Why：测试路径、废弃路径和失效路径必须能从列表里清掉，但“忘记路径”和“删除环境”必须严格分开。
- 恢复样本目录语义现已固定：
  - `missing_root` = 缺失路径
  - `invalid_root_file` = 非目录
  - `broken_env_root` = 损坏环境
  - `empty_root` = 新环境
  - `valid_legacy_env` = 可恢复 legacy 环境
  且样本根目录本身只是容器，不是可选环境。Why：这让 GUI 恢复态的手测和自动化回归都能围绕同一套可重复样本进行。

## 2026-03-20 GUI 收官补丁：数据目录移除、查询台真折叠、图标统一
- 数据目录切换确认框现在固定为三个动作：`切换并重启 / 移除此目录 / 取消`。Why：旧的 checkbox 语义不清，用户需要明确区分“切换到这个目录”和“只把这个目录从已保存列表里忘记”。
- `移除目录` 的语义继续固定为“只从 `known_data_roots` 忘记路径，不删除磁盘目录”。Why：环境列表管理和磁盘数据管理必须严格分开，避免误删用户环境。
- 查询台折叠现在改成“真收纳”：折叠后只保留搜索框、`查询` 按钮和内联 `展开` 按钮，标题区、提示区、状态区、阈值/来源/runtime 细节和底部说明区全部隐藏，并同步压缩 splitter 高度。Why：之前只隐藏文字但保留卡片高度，不能真正解决查询区占空间的问题。
- 查询台折叠按钮不再使用悬浮覆盖式布局，而是放进查询核心行并继续持久化 `qt_query_controls_collapsed`。Why：覆盖式按钮在窄窗口下容易错位，内联结构更稳。
- 桌面图标资产现在以 `icon/image.png` 为唯一源图，`resources/app_icon.png`、`resources/app_icon_32.png`、`resources/app_icon.ico` 只保留为派生交付资源。Why：运行时窗口图标、资源图标和打包 EXE 图标必须统一，不再允许多套旧图标真相并存。
- Windows 图标链现在同时绑定 `AppUserModelID`、`QApplication` 级图标、启动窗图标、主窗口图标和 frozen EXE 图标，并优先使用 `.ico`。Why：只换资源图片不足以保证任务栏图标更新，Windows 还依赖进程级身份与窗口级图标绑定链。

## 2026-03-23 Markdown 向量告警判定收口
- `service.py::query()` 现在只有在 `vector_backend` 真正启用时，才允许把 Markdown 查询计划进语义召回、`QUERY_PLAN.vector_enabled=true`、以及 `markdown_vector_index_missing` 这类向量告警。Why：之前只要 query profile 倾向语义检索，就算当前后端是 `disabled` 也会被误判成“缺少向量索引”，从而在已经完成普通建库后继续错误提示“请重新全量建库”。
- `QueryWorkspace` 的 runtime hint 现在在 runtime/config 同步后会主动清空旧的 `_query_runtime_warnings`。`ConfigWorkspace` 也会在模型准备完成、全量建库完成、清理完成后重新广播 live runtime snapshot。Why：这条黄条是“上一次查询的运行时诊断”，不是永久真相；当索引或模型状态已经被修复，再继续显示旧告警会制造假故障感。

## 2026-03-23 语义后端关闭态显式化与自动纠偏
- 真实用户数据排查确认过一条高频误导链：`config.json` 里 `vector_backend='disabled'` 时，Markdown 主索引依然可以是 `ready`，模型和 reranker 也都可以已经下载完成，但查询仍只会走 SQLite 字面检索。Why：旧 UI 把“模型已就绪”和“索引已建立”都显示成绿色，却没有把“语义后端其实还关着”显式说出来，用户会自然误以为语义召回已经参与查询。
- `service.status_snapshot()` 现在额外暴露 `vector_table_ready` / `vector_runtime_ready`，`ConfigWorkspace` 的索引芯片也分成三种稳定语义：`索引已建立`、`索引已建立（仅字面）`、`索引已建立（待补建语义向量）`。Why：Markdown 工作区的“索引 ready”不再等价于“语义 ready”；必须把字面层 ready 和语义层 ready 拆开呈现。
- `service.query()` 在 `vector_backend` 被关闭时会直接写入 `markdown_vector_backend_disabled` 诊断，而不是静默退回纯字面检索。Why：这类场景的正确解释是“后端被关了”，不是“没命中结果”也不是“缺少向量索引”；查询页必须把根因直接告诉用户。
- `ConfigWorkspace` 现在会在状态刷新 / 启动状态加载 / 模型下载完成后自动检查：如果本地语义模型已经就绪、runtime 也满足语义执行，但保存配置里还残留 `vector_backend='disabled'`，就会自动把后端切到 `lancedb` 并落盘。Why：用户主动下载了 `BAAI/bge-m3` 却仍然停留在 disabled，多半不是有意关闭语义，而是被旧默认值和旧 UI 误导；这类配置要自动纠偏，而不是继续让用户手动排雷。
- 自动纠偏后，如果当前工作区已经有 Markdown 普通索引但还没有 LanceDB 表，状态和日志会明确提示“还需要再执行一次全量建库，语义向量索引才会写入”。Why：下载模型只能补齐 runtime / 权重，不能倒推出现有工作区已经有向量表；用户需要知道下一步是“补建语义向量”，不是重复下载模型。

## 2026-03-24 扩展建库强化记忆
- 扩展格式建库现在正式进入“主线同级控制面、扩展自有数据面”的阶段。`PDF / Tika` 继续保留各自独立 parser、store、vector、runtime 与删除语义，但建库控制面开始对齐主线：全局/行级任务统一走 progress worker、统一 build ID、统一心跳与 watchdog 报告、统一 cancel/resume 状态合同。
- `omniclip_rag/extensions/build_state.py` 现在负责每条扩展管线自己的 `build_state.json / build_lease.json / diagnostics/`。这些文件必须和稳定的 `extensions_registry.json` 分离：registry 只保存稳定快照，build state 只保存易变的构建期状态。Why：把易变构建状态混进 registry 会让中断恢复、UI 状态和真实索引状态互相污染。
- 扩展状态机已经从旧的 `disabled / not_built / ready / stale / error` 扩展到 `DISABLED / NOT_BUILT / BUILDING / PAUSED / CANCELLING / INTERRUPTED / RESUMABLE / READY / WATCHING / STALE / ERROR`。Qt 侧不允许再用“猜测”去推断 READY；唯一真相源必须来自扩展服务层和构建状态文件。
- `READY` 与 `query_ready` 现在是两个不同概念：扩展查询只在 `index_state == READY` 且 `query_ready == true` 时开放；如果扩展索引已经完整但向量层不可用，状态必须诚实显示为 lexical-only，而不是假装整条扩展查询不可用。Why：扩展用户最需要的是“还能不能查”，不是“后端是不是全绿”。
- v1 扩展恢复策略已经锁死为“文件级保守恢复”：恢复单元只认“单文件 parser 完成 + store durable write 完成”，`vector` 阶段在恢复时一律从头重建；当前版本不做 chunk 级恢复，也不做部分 vector 续跑。Why：这比复杂 resume 更保守，但一致性风险远低。
- 当前版本明确不做扩展原子蓝绿切换；full rebuild 或异常中断期间，扩展查询直接不可用，不保留旧扩展索引继续服务。Why：扩展侧当前最大的痛点是解析链、可见性、取消与恢复，不是无缝切换。先把“真实可用”做稳，比追求切换优雅更重要。
- `PDF` 打包链已经把 `omniclip_rag.extensions.parsers.pdf` 显式加入 PyInstaller hidden imports；`pypdf` 也必须作为显式依赖存在于项目依赖声明中。Why：扩展 parser 是动态导入链，源码态可用不代表 EXE 态可用；这个坑已经真实踩过一次。
- 2026-03-24 补充：Frozen EXE 里的 `pypdf` 代码即使已打进去，也可能缺少 `dist-info`，导致 `importlib.metadata.version('pypdf')` 在 PDF 预检阶段误报 `PackageNotFoundError`。`extensions/service.py` 现在必须遵守“模块可导入优先、版本元数据缺失时回退到模块 `__version__` 或空字符串”的合同，不能再把“元数据缺失”误判成“PDF parser 不可用”；`OmniClipRAG.spec` 也同步补带 `copy_metadata('pypdf')`，尽量让打包态诊断信息保持完整。Why：这次真实故障不是 PDF 文件坏，而是 frozen 打包态的版本探测写得过严。
- 2026-03-24 再补充：`PDF preflight` 必须保持“轻量预检”合同，只允许做文件可读性、文件大小与精确页数读取，禁止再为了算页数去跑逐页 `extract_text()`。Why：预检的职责是快速确认“能不能做、多大范围”，不是提前半跑一遍正文解析；单文件 PDF 场景下全文抽取会把预检做得像卡死。
- 扩展预检/建库的 UI 现在必须区分“真的有可靠分母的确定性进度”和“任务还活着、但没有细粒度分母”的阶段；`inspect_pdf / inspect_tika / parse_pdf / parse_tika` 在单文件或无可靠分母场景下要用 busy/indeterminate + 已用时提示，而不是硬显示误导性的假百分比。Why：单文件 PDF/Tika 最容易让用户误以为任务停住，真正需要的是活性反馈，而不是精度虚假的进度条。
- Windows 下扩展 lease/build-state 的 JSON 写入必须容忍并发与瞬时共享冲突。当前实现已经增加写入锁、短重试，以及“lease 心跳写入失败只降级、不炸整轮建库”的保护。Why：lease 心跳是诊断与互斥辅助，不值得因为一次写盘抖动把整轮扩展建库打成失败。
- 2026-03-24 再补充：扩展 `scan_once` 以前在 `write_vector` 阶段仍走旧的 `vector_index.upsert(documents)` 黑盒路径，没有增量进度、也没有 cooperative cancel。结果是 Tika 在少量超大网页归一化成大量 chunk 后，会长时间停在静态 `92%`，用户点击停止也要等完整 upsert 结束才生效。现在 `PDF / Tika` 的增量向量写入必须复用和 full rebuild 同级的控制面：`upsert()` 接受 `on_progress / pause_event / cancel_event`，LanceDB 的增量 upsert 直接复用 `rebuild(reset_index=False)` 管线，`scan_once` 在 `write_vector` 期间必须持续推进 `92% -> 99%` 并允许及时响应取消。Why：这条链真正慢的不是 oversize 文件本身，而是增量向量落盘曾经是不可见、不可停的黑盒。
- 2026-03-24 再补充：`清理中断状态` 按钮的产品语义已经固定为“只清掉当前扩展残留的 interrupted/resume checkpoint，不删除原始文件，也不删除现有扩展索引数据；唯一后果是这次没做完的续建点作废，下次建库从头重来”。Why：用户需要一个明确、自救级但不危险的恢复入口，不能把它做成含糊的“清理一下试试”。
- Frozen 验证时，`desktop.py --selfcheck-query` 目前固定只查 `allowed_families=('markdown',)`；它适合验证打包后的 EXE 能否正常执行查询自检，但**不适合**拿来判断扩展命中链是否工作。Why：这条入口本来是 Markdown 查询诊断，不是扩展查询验收器；以后验证扩展 frozen 行为时不要被“自检能跑但没扩展命中”误导。
- 当前 PyInstaller `warn-OmniClipRAG.txt` 里仍可能出现 `pypdf` 的可选 `Crypto.*` 提供者告警，以及 `modelscope.hub*` 的条件导入告警；只要没有重新出现 `omniclip_rag.extensions.parsers.pdf / tika` 缺失，就不应把这些可选告警误判成扩展 parser 漏包回归。Why：扩展建库强化这轮真正要封死的是 parser 漏包与依赖缺失，不是顺手清零所有条件导入告警。

## 2026-03-26 Markdown 多库来源目录与扩展来源目录交互收口
- Markdown 主线现在正式采用“三层语义分离”模型：`vault_path` 表示唯一当前笔记库，`vault_paths` 表示已保存来源目录全集，`md_selected_vault_paths` 表示当前启用的搜索/批处理/统计范围。Why：`当前` 与 `启用` 是两种不同职责，不能再用一个 Combo 和一个输入框把它们混成同一个概念。
- `开始` 页的 `MD文件来源目录` 只是 UI/调度控制台，不改变 Markdown 底层“每个 vault 对应一个独立 workspace / 索引 / watcher”的事实。Why：这轮改造的目标是多库管理与批处理，不是把多个 Markdown 库混写进同一个工作区。
- Markdown 多库任务采取 **UI 层顺序编排**：预检、全量建库、监听启动/停止都只复用现有单库服务接口，由协调器对勾选快照逐库顺序调度；同一时刻只允许 1 个 Markdown 主线任务运行。Why：用户明确担心两个库同时建库把硬件资源打满，顺序编排是当前阶段最稳的多库管理策略。
- Markdown 批处理的停止语义已锁定为“停止当前并终止队列”；任务开始瞬间会冻结一份勾选快照，运行中用户再改勾选只影响下一次任务。Why：否则会出现用户以为停了但后续库继续跑，或运行中目标集合动态漂移导致状态错乱。
- 多库查询固定为 **fan-out + merge**：查询页对 `md_selected_vault_paths` 里的每个库构造独立 `config + paths + service` 顺序查询，再按全局分数统一混排，并为每条结果带上来源笔记库标签。Why：要支持多库搜索，但不应把主线 `service.query()` 重写成多库存储协议。
- 聚合统计（文件 / 片段 / 引用）只统计“已勾选且 ready 的库”；`pending / missing / error` 库不并入总数，只单独提示。Why：多库场景下最容易出现“聚合数看起来很大但其实包含未就绪库”的假统计。
- Markdown 多 watcher 现在必须走 `vault_path -> WatchWorker/session` 的管理模型：顺序启动、并发驻留；程序关闭、移除来源目录、数据目录切换前都必须先清掉对应 watcher。Why：开始页从单库切到多库后，监听生命周期不能再绑死在单一 `_watch_worker` 上。
- Markdown 行级 `移除` 的语义固定为“只从已保存列表与勾选范围里忘记这个来源目录，并在必要时切换当前库/停掉 watcher”，**不删除原始笔记、工作区或索引**。Why：来源目录管理和数据清理必须严格分开，不能把“移除来源目录”做成危险动作。
- 扩展来源目录取消勾选现在正式拆成三选项：`取消勾选（不搜索） / 清除索引 / 取消`。Why：旧的 `Yes/No` 过于生硬，默认把“不想参与搜索”强绑成“必须删索引”不符合用户心智。
- 扩展来源目录的 `selected` 只再表示“是否参与搜索”，不再表示“是否允许管理”；未勾选但保留索引的来源目录仍然允许执行预检、建库和删索引。Why：来源目录的管理权和搜索参与权是两件不同的事，UI 不能再把它们绑死在一个布尔值上。
- `probe_data_root()` 现在容忍“只有 `shared/logs` 痕迹、但还没形成完整环境”的 partial root，把它视为 `new` 而不是损坏环境。Why：最近多库/扩展任务会更早创建日志目录，不能因为这些早期日志痕迹把一个尚未正式落地的目录误判成 broken environment。

## 2026-03-26 配置页排序、主库/纳入范围与全局悬浮说明收口
- 配置页页签顺序现在固定为：`开始 -> 设置 -> Runtime -> 拓展格式 -> 检索强化 -> UI -> 数据`。Why：当前产品心智已经不再是“先 UI、再数据”的早期结构；Runtime 与扩展格式都已经成为用户的高频入口，页签顺序必须跟真实使用路径对齐。
- `MD文件来源目录` 的表头口径正式从 `当前 / 启用` 收口为 `主库 / 纳入范围`，但底层模型保持不变：`vault_path` 仍然是唯一主库，`md_selected_vault_paths` 仍然是多选范围。Why：这轮要改的是认知层，不是底层存储协议；用户需要更白话的 UI 名字，但代码里不应该为此重写已稳定的多库模型。
- `主库` 的产品合同现在已经固定：只能有一个、不可留空、可切换且切换后其他行自动取消；`纳入范围` 是独立的多选集合，决定 Markdown 搜索、批量建库、热监听和聚合统计范围。Why：这两个概念解决的是“默认锚点”和“参与范围”两件不同的事，UI 必须显式解释，不能再让用户自己猜。
- 全局 tooltip 现在作为统一 UI 能力接管，而不是零散 `setToolTip()` 文案堆砌：`AppConfig.ui_tooltips_enabled` 保存用户偏好，UI 页提供全局开关，应用主题统一控制 `QToolTip` 的样式与显示延迟，关闭后全局不显示。Why：软件功能复杂度已经超过“默认系统黄框即可”的阶段，悬浮说明必须成为一致的认知层基建。
- Tooltip 的覆盖范围故意保持克制：只优先解释高价值概念与关键动作，如 `主库 / 纳入范围 / 数据目录 / 预检查空间时间 / 全量建库 / 启动热监听 / 清理中断状态 / 扩展来源目录的取消勾选语义`。Why：悬浮说明的职责是降低认知门槛，不是把所有控件都做成噪音源。
- `开始` 页 quick-start 已经从旧的单库三步说明改成四步模型：`数据目录 -> MD文件来源目录 -> Runtime/模型 -> 预检查与建库`，并明确区分 Markdown 主线与 PDF/Tika 扩展来源目录。Why：开始页必须反映当前软件真实结构，否则用户会被旧时代的单库文案误导。
