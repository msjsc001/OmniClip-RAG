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

- 扩展格式（第一批：PDF / HTML / MHTML）已经明确不能作为 Markdown 主链里的几个条件分支去实现，而必须作为平行的隔离子系统推进：UI、状态、索引、热监听、删除/重建都要独立；查询层再统一融合。
- 这项工作的执行蓝图已固定到 `plans/扩展格式隔离子系统实施计划.md`。后续无论在哪个聊天窗继续实现，必须先读取该文档，再继续写代码。
- 该功能的硬约束是：扩展格式默认关闭时，当前 Markdown 主链的行为必须与“该功能不存在”完全等价；任何破坏该约束的实现都不得合入。
