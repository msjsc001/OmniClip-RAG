# Architecture Notes

## Release Boundary For V0.1.0

`V0.1.0` is not trying to ship a giant all-in-one AI platform.

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

## Intentional Tradeoffs In V0.1.0

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

### 8. Desktop UX must be newcomer-first

The desktop UI is no longer allowed to assume the user already understands RAG terminology or the product workflow.

Current UX rules:

- the first screen must explain the first three actions in plain language,
- recommended defaults must already be filled in,
- advanced options must stay hidden until explicitly expanded,
- missing prerequisites such as local models must surface as clear prompts instead of silent stalls,
- hover tooltips must explain settings and buttons without forcing the user to open external docs.

Why: a local-first desktop tool fails its job if the user has to reverse-engineer the interface before they can trust it.

### 9. UI text and behavior hints live outside the window layout

Current split:

- `ui_i18n.py`: bilingual UI strings and tooltip text,
- `ui_tooltip.py`: hover help behavior,
- `gui.py`: layout, state, background-task orchestration.

Why: language switching, wording upgrades, and future docs-quality polishing should not require invasive changes across the GUI layout code.

### 10. Brand assets are generated locally and packaged explicitly

Current asset policy:

- app icons are generated from a deterministic local script,
- source runs load icon resources from `resources/`,
- EXE builds bundle the same resources and embed the `.ico` into the executable.

Why: the desktop app, taskbar icon, and packaged EXE must present a consistent identity without adding heavy image-tool dependencies.



