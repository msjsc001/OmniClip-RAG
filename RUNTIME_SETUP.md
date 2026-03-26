# Runtime Setup

The official Windows build of OmniClip RAG / 方寸引 intentionally stays lean.
It does **not** bundle heavyweight optional local-AI runtimes such as `torch`, `sentence-transformers`, `lancedb`, `pyarrow`, or `onnxruntime` into the main EXE package.

Why:

- the desktop package should stay small and replaceable;
- user data, model cache, and Runtime payloads should remain outside the EXE tree;
- CPU / CUDA choices should remain explicit and repairable;
- a new packaged version should **reuse** an existing healthy Runtime instead of forcing a full redownload.

## Runtime Layout In `v0.3.3`

Starting with the current packaged design, OmniClip distinguishes two Runtime paths:

- **Active Runtime**: the Runtime the app is currently using right now
- **Preferred Install / Repair Target**: where future Runtime downloads and repairs should be written

For packaged builds, the preferred target now defaults to:

```text
%APPDATA%\OmniClip RAG\shared\runtime
```

This means:

- new versions do not need their own private full Runtime copy;
- future repairs converge into one shared Runtime root;
- older healthy Runtime folders can still be reused as legacy sources.

The app can still auto-detect and reuse valid legacy runtimes from places such as:

- the current packaged folder's `runtime/`
- a manually moved `runtime/`
- sibling packaged folders like `OmniClipRAG-v0.3.0/runtime`

So version updates should no longer imply "download everything again."

## What You Need For Local Embeddings

To enable model warmup, full rebuild, semantic query, and GPU acceleration on the packaged app, install the required Runtime components through the Runtime page or the bundled PowerShell installer.

The packaged app still ships `InstallRuntime.ps1` next to `OmniClipRAG.exe`, and the GUI Runtime page uses the same installation chain.
That installer now prefers the **bundled Python runtime inside the app package** and writes to the shared Runtime target by default instead of only writing into the current EXE folder.
Runtime manifests now lock an **exact wheel set** per component, so installation is no longer left to live pip dependency resolution from loose version ranges.

## CPU Runtime

If you are already inside the app folder:

```powershell
.\InstallRuntime.ps1 -Profile cpu
```

If you are in another folder, call the full path to `InstallRuntime.ps1`.

Expected size:

- final disk usage: about `1.5 GB - 2.5 GB`
- download volume: about `1 GB - 2.5 GB`

## NVIDIA CUDA Runtime

If you are already inside the app folder:

```powershell
.\InstallRuntime.ps1 -Profile cuda
```

If you are in another folder, call the full path to `InstallRuntime.ps1`.

Expected size:

- final disk usage: about `4.3 GB - 4.9 GB`
- download volume: about `3 GB - 5 GB`

Notes:

- `cuda` requires an NVIDIA GPU, working drivers, and a compatible PyTorch CUDA stack.
- a working system CUDA installation alone is **not** enough; OmniClip still needs its own Runtime payloads.
- the installer now uses the **bundled Python runtime** that ships with the packaged GUI build, so users do **not** need to install Python separately.
- Runtime installation is staged as: download wheels -> verify files -> offline install -> validate modules.
- the packaged EXE remains unchanged; only the external Runtime sidecar grows.
- Runtime installation also brings in the local vector stack used by semantic indexing and query.
- on machines without an NVIDIA GPU, CUDA is still **not required**, but the Runtime page now allows you to manually download / repair the CUDA component anyway so you can test the installation pipeline. A successful download there only means **installed on disk**; it does **not** mean the current machine is GPU-ready.

## Pending Updates And Restart Behavior

If OmniClip is running while you repair or download Runtime, the installer may stage the payload into a pending area first.

In that case:

- the current session keeps using the active Runtime it already has;
- the pending Runtime update is applied on the next launch;
- the Runtime page can show both the active Runtime path and the preferred install target so you can tell what is happening.

## If Python Is Not Installed

That is now fine for packaged GUI users.

- You do **not** need to install Python separately.
- `InstallRuntime.ps1` and the Runtime page will use the Python runtime bundled with the app.
- Only source-tree developers should care about a system Python fallback.

## If Runtime Installation Fails

The installer now writes structured diagnostics under the active data root:

```text
<active data root>\shared\logs\runtime
```

When a failure happens, check the latest JSON report there. It records:

- which profile/component was being installed
- which wheel download failed
- whether the failure happened during download, verification, install, or validation
- the exact Python/runtime root that was used
- the current stage and artifact counters that the GUI/terminal progress view was reading

## Model Files Are Still Separate

Runtime payloads and local model files are different things.

- Runtime payloads provide the executable libraries needed for local embedding / vector work.
- model files remain in the OmniClip data/cache area and are downloaded or managed separately.

This separation is intentional: Runtime should be shareable and repairable across versions, while model caches stay under the app's own data management.
