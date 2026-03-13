# OmniClip RAG / 方寸引 v0.2.3

## Summary

`v0.2.3` is a focused usability release built on top of `v0.2.2`.

The goal of this release is direct: when `BAAI/bge-m3` is missing, the software should no longer leave the user with only a vague hint. It now gives a truthful automatic path, a truthful manual path, and Windows-ready commands that already point at the correct local cache folder.

## Highlights

- Added an explicit `automatic download / manual download` split when `BAAI/bge-m3` is missing.
- Added a copyable Qt manual-download dialog with the target folder, Hugging Face CLI bootstrap command, official source link, mirror link, and ready-to-run Windows terminal commands.
- Reused the same manual-command generator in the legacy Tk path and copy the full instruction block to the clipboard before showing the old message box.
- Refreshed README positioning and wrote down the isolated extension-format subsystem plan so the next large feature can resume cleanly.

## Release Shape

- GitHub source push: code, docs, tests, release notes
- GitHub release asset: lightweight Windows package zip built from `dist/OmniClipRAG-v0.2.3/`
- Included in release asset: `OmniClipRAG.exe`, `_internal`, `InstallRuntime.ps1`, `RUNTIME_SETUP.md`
- Not included in release asset: local `runtime/`, model cache, user data, indexes, exports, logs

## Notes

The manual model-download commands are generated from the real local AppData paths on the machine that runs the app. The target folder is created first, and the user is told to restart the app or click the download button again after the files are in place so the local integrity check can run immediately.
