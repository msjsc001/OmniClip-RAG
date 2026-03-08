# Runtime Setup

The official Windows release of OmniClip RAG / 方寸引 is intentionally a lean desktop package.
It does **not** bundle very large optional AI runtimes such as `torch`, `sentence-transformers`, or `onnxruntime` into the main app package.

Why:

- the core program should stay clean and reasonably sized;
- model files are already downloaded separately by the user;
- GPU/CPU runtime choice should remain under the user's control.

## What you need for local embeddings

To enable model warmup, full rebuild, and semantic query on the packaged app, install a runtime into the app's `runtime/` folder.

The packaged app includes `InstallRuntime.ps1` next to `OmniClipRAG.exe`.

### CPU runtime

If you are already inside the app folder:

```powershell
.\InstallRuntime.ps1 -Profile cpu
```

If you are in another folder, use the full path to `InstallRuntime.ps1`.

Expected size:

- final disk usage: about `1.3 GB - 2.0 GB`
- download volume: about `1 GB - 2 GB`

### NVIDIA CUDA runtime

If you are already inside the app folder:

```powershell
.\InstallRuntime.ps1 -Profile cuda
```

If you are in another folder, use the full path to `InstallRuntime.ps1`.

Expected size:

- final disk usage: about `4.3 GB - 4.6 GB`
- download volume: about `3 GB - 5 GB`

Notes:

- `cuda` requires an NVIDIA GPU, working drivers, and a compatible PyTorch CUDA environment. A working `nvcc` on the system is not enough by itself; this lean app package still needs its own runtime installed into `runtime/`.
- The installer script uses your system Python to download the runtime into the app-local `runtime/` folder.
- The script creates or updates the app-local `runtime/` folder next to `OmniClipRAG.exe`.
- The main app package remains unchanged; only the optional runtime folder grows.

## If Python is not installed

Install Python 3.13 or newer first, then re-run `InstallRuntime.ps1`.

## Model files are still separate

Model files are **not** bundled into the app package.
They remain in your OmniClip data directory and are downloaded or managed separately by the user.
