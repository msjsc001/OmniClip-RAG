# OmniClip RAG v0.1.1

`v0.1.1` packages the desktop hardening work after the first public release candidate.

This update is about making the app easier to start, safer to leave running, and much more reliable on real local vaults.

## Highlights

- Newcomer-first desktop onboarding with clearer wording, bilingual UI switching, and hover tooltips
- Multi-vault switching with isolated per-vault workspaces
- Shared cross-vault model cache and shared logs under `shared/`
- Manual model-download fallback with `hf-mirror.com`, Hugging Face links, and explicit target folders
- Space-and-time prechecks before model bootstrap or indexing
- Resumable full rebuilds after close, crash, or power loss
- Real **pause / resume** for full rebuilds so users can temporarily yield CPU
- Safer local-model loading that avoids unnecessary remote Hugging Face calls when the local cache is already complete
- Hardened indexing for unreadable Markdown files, duplicate Logseq block ids, and stale layout values

## What Changed Since v0.1.0

### Desktop workflow

- Reworked the GUI into a clearer newcomer-first workflow with better defaults and better wording.
- Added bilingual runtime localization for both static UI labels and live task/status text.
- Added live task progress, elapsed time, ETA feedback, and better first-run prompts.
- Added remembered window geometry and split-pane layout.

### Data model

- Split data into `shared/` and `workspaces/<workspace-id>/`.
- Shared assets now hold cross-vault model cache and logs.
- Vault-specific assets now stay isolated in per-vault workspaces.
- Legacy workspace-local cache/log data migrates forward automatically.

### Model handling

- Added model self-check so the app recognizes a complete local model and avoids redundant downloads.
- Added auto-download / manual-download choice before bootstrap.
- Added explicit manual-download instructions and target directories.
- Tightened local-model loading so a ready local model does not re-hit Hugging Face during query or rebuild.

### Rebuild reliability

- Added unfinished-build persistence and one-click resume.
- Added pause / resume controls for full rebuild.
- Added pause points in file parsing, rendered-text expansion, and vector batching.
- Hardened rebuild behavior for duplicate Logseq block ids and unreadable Markdown files.

## Validation

This update has been verified with:

- automated unit tests,
- GUI startup smoke checks,
- EXE build verification,
- EXE startup smoke checks,
- sample vault indexing and query checks.

## Documentation

- English README: [README.md](../README.md)
- Chinese README: [README.zh-CN.md](../README.zh-CN.md)
- Architecture: [ARCHITECTURE.md](../ARCHITECTURE.md)
- Changelog: [CHANGELOG.md](../CHANGELOG.md)

## Short Release Summary

OmniClip RAG v0.1.1 turns the first public desktop candidate into a much more practical daily-use build: clearer onboarding, better data boundaries, safer model handling, and interrupt-resilient indexing.
