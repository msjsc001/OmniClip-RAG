# Architecture Notes

## Release Boundary For V0.1.2

`V0.1.2` is not trying to ship a giant all-in-one AI platform.

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

### 5. Watch mode must be externally stoppable

CLI-only blocking watch was not enough for a desktop app.

The GUI-required watch contract now includes:

- start,
- stop,
- background execution,
- update callbacks,
- visible status feedback.

Why: a desktop app must let the user manage background listeners safely and explicitly.

### 6. User data defaults to `%APPDATA%`, but the app must recover when that path is blocked

The app still prefers `%APPDATA%\OmniClip RAG`, but now falls back to a writable directory automatically when needed.

Why: the storage convention is correct, but the product cannot assume every runtime environment has working permissions there.

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

## Intentional Tradeoffs In V0.1.2

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
- advanced options must stay hidden until explicitly expanded,
- missing prerequisites such as local models must surface as clear prompts instead of silent stalls,
- model-download prompts must also provide a manual mirror fallback and the exact local directory for users who need to download files themselves,
- hover tooltips must explain settings and buttons without forcing the user to open external docs,
- the left-side workspace area must stay usable on common horizontal displays by using tabbed sections and per-tab scrolling instead of one long stacked form,
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

### 16. A ready local model must never trigger fresh network access

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

