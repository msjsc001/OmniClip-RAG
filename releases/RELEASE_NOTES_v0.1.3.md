# OmniClip RAG v0.1.3

`v0.1.3` is a packaging hotfix release for Windows.

## Highlights

- fixed the packaged EXE startup crash caused by a missing `pyarrow.libs` directory,
- kept the main release lean instead of rebundling very large optional AI runtimes,
- preserved the separate runtime-install flow through `RUNTIME_SETUP.md` and `InstallRuntime.ps1`.

## What Changed Since v0.1.2

### Windows packaging

- The onedir build now copies `pyarrow.libs` into `_internal/pyarrow.libs`.
- This restores the required `pyarrow` dependency chain used by `lancedb` during desktop startup.

### Release strategy

- The official app package remains lightweight.
- Models are still user-managed and stored separately.
- Optional heavy runtimes remain outside the main package.

## Validation

This hotfix has been validated with:

- automated unit tests,
- EXE startup smoke checks,
- packaged size checks after rebuild.

## Documentation

- English README: [README.md](../README.md)
- Chinese README: [README.zh-CN.md](../README.zh-CN.md)
- Architecture: [ARCHITECTURE.md](../ARCHITECTURE.md)
- Changelog: [CHANGELOG.md](../CHANGELOG.md)
- Runtime Setup: [RUNTIME_SETUP.md](../RUNTIME_SETUP.md)
