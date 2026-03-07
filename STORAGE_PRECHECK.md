# Storage Precheck

OmniClip RAG performs a real preflight estimation before model bootstrap and full indexing.

The goal is simple: **fail early when disk space or local model prerequisites are not ready**, instead of letting a long indexing run die halfway through.

## What Gets Estimated

The preflight process inspects the actual vault parse result and estimates:

- raw vault size,
- parsed chunk count,
- reference count,
- SQLite metadata footprint,
- FTS footprint,
- vector index footprint,
- local model cache footprint,
- temporary peak usage,
- safety margin,
- available free space on the target drive.

This is not a naive file-size-only estimate.

## Why This Exists

For a local-first RAG, the first run is where users most often lose time:

- model download may be large,
- vector storage grows with chunk count,
- Logseq-heavy vaults expand more because refs and embeds increase rendered text size,
- Windows environments are often space-fragile and permission-fragile.

The preflight step is the guardrail against that.

## Current Practical Guidance

For the current default stack:

- backend: `LanceDB`
- model: `BAAI/bge-m3`
- runtime: `torch`

A first local run on Windows should generally reserve **8 GB to 10 GB** of free disk space.

That number is intentionally conservative.

## Risk Levels

The current implementation emits these practical states:

- `ok`: enough space, safe to continue
- `tight`: probably enough, but margin is thin
- `insufficient`: not enough free space for a safe first run
- `blocked`: local-only mode is enabled, but the local model cache is missing or incomplete

## What Happens On Failure

By default:

- bootstrap stops,
- indexing stops,
- the report is shown to the user,
- the run is recorded in SQLite history.

Users can still override the stop behavior explicitly if they know what they are doing.

## Stored History

Each preflight run is written to the `preflight_runs` table in:

```text
state/omniclip.sqlite3
```

This makes the estimate auditable instead of ephemeral.

## What Is Checked Beyond Space

The preflight layer also checks whether local-only vector mode is realistic.

If local-only mode is enabled, the system verifies that the local model directory is actually usable, not just present as an incomplete partial download.

That means:

- config files alone are not enough,
- actual model weight files must exist,
- incomplete caches are treated as blocked state.

## Product Principle

The storage precheck is not cosmetic.
It is part of the reliability model of the product.

For a local tool, predictable failure is better than optimistic failure.
