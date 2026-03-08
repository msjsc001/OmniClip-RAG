# OmniClip RAG v0.1.4

`v0.1.4` is a runtime-messaging hotfix release.

## Highlights

- fixed the missing `_runtime_dependency_message()` path so runtime-missing errors no longer collapse into a `NameError`,
- clarified CUDA reporting so the app now separates “system CUDA exists” from “this lean package still needs its own runtime install”,
- kept the lean-release strategy unchanged while making the missing-runtime path much easier to understand.

## What Changed Since v0.1.3

### Runtime error handling

- Packaged builds now raise a clean `RuntimeDependencyError` when `sentence_transformers` is missing.
- The error now tells the user exactly which `InstallRuntime.ps1` command to run and where to run it.

### CUDA capability messaging

- The settings panel can now acknowledge an installed NVIDIA GPU and system CUDA toolkit without pretending the app is already CUDA-ready.
- The device summary now tells the user when the missing piece is the app-local runtime, not the system driver or toolkit.

## Validation

This hotfix has been validated with:

- Python compile checks,
- targeted GUI/runtime messaging tests,
- targeted vector-index regression tests,
- packaged EXE startup smoke checks.

## Documentation

- English README: [README.md](../README.md)
- Chinese README: [README.zh-CN.md](../README.zh-CN.md)
- Architecture: [ARCHITECTURE.md](../ARCHITECTURE.md)
- Changelog: [CHANGELOG.md](../CHANGELOG.md)
- Runtime Setup: [RUNTIME_SETUP.md](../RUNTIME_SETUP.md)
