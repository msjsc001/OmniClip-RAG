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
- model files are downloaded into explicit local model directories,
- Xet transport is disabled,
- default user cache assumptions are avoided,
- local-only mode is supported for bootstrap/query/index flows.

Why: the default Windows cache path is too easy to corrupt with permission issues, symlink edge cases, and partial downloads.

### 8. Unreadable Markdown files must be skippable, not fatal

Current fault-tolerance rule:

- preflight skips unreadable `.md` files and records that fact in the estimate notes,
- rebuild/reindex skips unreadable `.md` files instead of aborting the whole run,
- if every discovered Markdown file is unreadable, preflight blocks the workflow and tells the user the chosen folder is not a real vault root.

Why: users can accidentally point the app at a home directory, browser profile, or synced workspace that contains Markdown files they do not actually own or cannot read. One bad file must not collapse the whole product.

## Module Boundary

- `omniclip_rag.config`: configuration and data paths
- `omniclip_rag.parser`: vault parsing
- `omniclip_rag.storage`: SQLite, FTS, refs, preflight history
- `omniclip_rag.preflight`: disk estimation
- `omniclip_rag.vector_index`: embeddings and LanceDB
- `omniclip_rag.service`: indexing, querying, watching, cleanup
- `omniclip_rag.gui`: desktop interaction layer
- `omniclip_rag.clipboard`: clipboard handoff

## Verified So Far

Current validation includes:

- sample vault indexing with `3` files, `30` chunks, and `10` refs,
- successful `bge-m3` warmup with `1024`-dimensional embeddings,
- successful bootstrap / index / query / watch flows,
- successful GUI startup and shutdown,
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

1. add reranker support,
2. add tray mode and global hotkeys,
3. add richer settings panels,
4. reduce model and package footprint further.

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
### 13. Lean packaged builds must discover external runtime installs explicitly

### 14. Late-stage build optimizations are independent performance tracks

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


Current runtime policy:

- the public Windows package stays lean and does not bundle `torch` or `sentence-transformers`,
- optional local runtimes are installed into `dist/OmniClipRAG/runtime/`,
- the runtime installer writes `_runtime_bootstrap.json` with the Python stdlib and DLL locations used during installation,
- packaged startup reads that marker and restores the search paths before probing `torch` or `sentence_transformers`.

Why: a lean PyInstaller build cannot assume that every stdlib module or DLL needed by an externally installed runtime is already discoverable inside the frozen app environment.

### 15. Rebuilds must preserve an existing local runtime install

Current packaging rule:

- rebuilding the EXE must not wipe a user's already-downloaded `dist/OmniClipRAG/runtime/` directory,
- source control still ignores `runtime/`, `dist/`, and other large local artifacts,
- GitHub releases may ship a lightweight app package, but the heavyweight runtime remains a user-installed optional layer.

Why: deleting a multi-gigabyte local runtime on every rebuild is wasteful and makes packaged testing far slower than necessary.


Current storage policy:

- the user still chooses one global data root,
- the saved config stays at that global root,
- common data now lives under `shared/`,
- each vault gets a deterministic workspace under `workspaces/<workspace-id>/`,
- the vault workspace owns only vault-specific state such as SQLite, LanceDB state, and exported context packs,
- the shared area owns only cross-vault data such as model cache and general logs,
- legacy per-workspace cache/log folders are migrated forward into the shared area automatically.

Why: one shared data directory is convenient, but shared data and vault-local data should not be mixed. The product stays easier to manage when common assets are centralized and vault-specific assets remain isolated.

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
