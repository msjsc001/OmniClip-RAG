# OmniClip RAG / 方寸引 v0.3.2

## Summary

This GitHub release is the first public release after `v0.3.0`.
It therefore covers the accumulated work that happened along the internal `v0.3.1 -> v0.3.2` path instead of only listing the final patch delta.

In practical terms, this release closes two packaging-level problems left after `v0.3.0`:

- cross-version Runtime reuse was still too fragile;
- the Tika format picker still depended too heavily on a locally installed Tika JAR.

This release makes the packaged app much more honest about Runtime ownership and makes the Tika picker useful before runtime installation.

## Highlights

- The `v0.3.1` line of work is included here even though it did not ship as a separate public Release:
  - Runtime component management, Runtime page UX, and packaged Runtime repair flows were already merged into the main code line
  - isolated PDF / Tika extension infrastructure was already in place
  - Qt-only desktop delivery and the newer packaged-debugging / self-check spine were already established
- Runtime is now designed around a **shared AppData root**:
  - default install/repair target: `%APPDATA%\OmniClip RAG\shared\runtime`
  - legacy packaged runtimes are still auto-detected and reused
  - future repairs no longer depend on the current EXE folder staying unchanged
- Runtime component manifests are now **relocatable**:
  - new installs prefer relative paths
  - old absolute-path manifests are still salvaged
  - versioned payload folders such as `semantic-core-<timestamp>` can be discovered automatically
- Tika format selection is now **full-catalog first**:
  - installed Tika JAR if available
  - bundled fallback suffix list if no JAR is installed
  - curated defaults only as the last fallback
  - the current picker exposes the larger Tika-backed format universe instead of falling back to the old tiny list
- Packaged builds remain **lean**:
  - no bundled Runtime payload
  - no bundled Tika server JAR
  - no bundled JRE
  - but the Tika suffix fallback resource is now inside the package

## Included Since `v0.3.0`

### Runtime / packaged delivery

- Runtime component repair and cleanup UX is part of the desktop app
- packaged Runtime support files and Runtime page guidance are included in the build
- Runtime install target now converges into the shared AppData Runtime root
- old absolute component manifests are salvaged and new installs prefer relative paths
- valid legacy runtimes can be reused across version folders instead of forcing a full redownload

### Extension formats

- PDF remains on a dedicated isolated parse / index / query path
- Tika remains physically isolated from Markdown and PDF stores
- Tika format selection is now usable before local Tika installation because of the packaged suffix fallback catalog

### Mainline delivery shape

- the repository is now Qt-only for desktop UI
- the packaged app keeps Runtime, model caches, Tika JARs, and JREs outside the EXE payload
- docs and plans now record the Runtime RCA, GPU Runtime / UX closure track, and the Runtime cross-version stabilization work in-repo

## Release Shape

- GitHub source push: code, docs, tests, release notes
- Packaged build output validated locally:
  - `dist/OmniClipRAG-v0.3.2/`
  - `dist/OmniClipRAG-v0.3.2-win64.zip`
- Still not bundled into the app package:
  - Runtime payloads
  - Tika JAR / JRE
  - model cache
  - user data / indexes / logs / exports

## Notes

This release is primarily about making the Runtime and Tika behavior match the original product intent:

- users should not be forced to redownload Runtime on every version bump;
- users should be able to see the Tika format universe before deciding whether to install Tika.
