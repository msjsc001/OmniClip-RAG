# OmniClip RAG / 方寸引 v0.3.3

## Summary

`v0.3.3` is the Tika stability closure release after `v0.3.2`.

This release focuses on one practical outcome: if a user selects a Tika-supported format and the file itself is healthy, the build pipeline should actually index it instead of reporting a vague "skipped" result. At the same time, the Tika runtime installer should finally show visible progress instead of behaving like a black box.

## Highlights

- Tika indexing is now **compatibility-first**:
  - first try `PUT /tika` with `Accept: text/plain`
  - then fall back to `PUT /rmeta` with `Accept: application/json`
  - only fail after both strategies are exhausted
- Tika success is now defined as **extractable body text**, not "XHTML must exist"
- expected local-file skips are now separated from real parser failures:
  - zero-byte or unreadable files stay skippable
  - sidecar/protocol failures are reported as true failures
- the Tika Runtime card now shows **inline install progress**:
  - current stage
  - current download item
  - byte progress
  - install target
- project docs were refreshed to match the current product shape:
  - `Core Features / 核心特性`
  - explicit open-source acknowledgements
  - a permanent in-repo Tika stabilization plan

## Included In This Release

### Tika build/index path

- replaced the old XHTML-only parse contract with a compatibility-first multi-strategy parser
- added structured Tika parse results and failure reasons
- added unified content normalization for plain text, rmeta JSON, and XHTML
- improved build reporting so users can distinguish:
  - indexed files
  - expected skips
  - true parse failures

### Tika installer UX

- switched the Tika auto-install flow to a progress-aware worker path
- surfaced installation stage, byte progress, current item, and target directory inside the page
- improved busy/disabled state handling while install or redetect operations are running

### Documentation

- updated README and README.zh-CN to `v0.3.3`
- renamed the feature overview section to `Core Features / 核心特性`
- added an open-source acknowledgements section before the license block
- recorded this stabilization work in [Tika建库稳定性与安装进度闭环计划](../plans/Tika建库稳定性与安装进度闭环计划.md)

## Release Shape

- GitHub source push: code, docs, tests, release notes
- Packaged build output:
  - `dist/OmniClipRAG-v0.3.3/`
  - `dist/OmniClipRAG-v0.3.3-win64.zip`
- Still intentionally not bundled into the app package:
  - Runtime payloads
  - Tika JAR / JRE
  - model cache
  - user data / indexes / logs / exports

## Notes

This release does not change the architecture boundary that matters most:

- PDF still stays on its own isolated route
- Tika still stays isolated from the Markdown and PDF stores
- the packaged app remains lean, while heavy runtime assets stay outside the EXE payload
