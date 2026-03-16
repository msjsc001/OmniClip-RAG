# OmniClip RAG / 方寸引 v0.3.0

## Summary

`v0.3.0` is a source-milestone release built on top of `v0.2.4`.

This version merges the extension-format subsystem, the componentized Runtime manager, the Qt-only desktop cleanup, and the in-repo Markdown query/runtime RCA framework into the main code line.

It is intentionally released as **code + docs only**. The packaged EXE path is still being polished, so this GitHub Release does not attach a Windows binary asset.

## Highlights

- Added the isolated `extensions/` subsystem:
  - dedicated PDF parsing, indexing, and querying
  - Tika sidecar runtime management
  - Tika format white-listing and compatibility tiers
  - isolated extension watch/build state
  - cross-source query broker support
- Added a componentized Runtime management experience:
  - semantic core
  - vector storage support
  - optional NVIDIA / CUDA acceleration
  - pending-update staging
  - official / mirror repair flows
- Removed the legacy Tk desktop UI from the repository and kept the desktop shell Qt-only.
- Added Markdown main-query/runtime RCA infrastructure:
  - packaged self-check query path
  - query trace planning
  - runtime/index/workspace fingerprints
  - in-repo RCA plan for future continuation

## Release Shape

- GitHub source push: code, docs, tests, release notes
- GitHub Release assets: none
- Not shipped in this release:
  - Windows EXE package
  - local runtime folder
  - model cache
  - user data / indexes / logs / exports

## Notes

This release marks the point where the extension-format and Runtime-management architecture enters the main branch.

Some packaged EXE behavior is still under active final validation, especially around Runtime UX and Markdown GUI query parity, so the Windows binary is intentionally withheld from this Release.
