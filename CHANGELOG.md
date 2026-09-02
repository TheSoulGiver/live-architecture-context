# Changelog

## Unreleased

- Added three on-demand Codex skills (context, impact, recovery) and a thin
  no-hook plugin manifest; the managed AGENTS fallback now uses a short
  architecture reflex instead of a per-task procedure.
- Added fixed-size anonymous result categories to telemetry and usage receipts.
  `actionable_result_rate` is explicitly a result proxy, not a claim that an
  Agent used the result or that a task succeeded.

## v0.1.5

- Made freshness compare canonical evidence/config semantics rather than the
  availability or representation of a Git revision, so read-only sandboxes and
  unrelated commits do not falsely mark context stale.

## v0.1.4

- Clarified that only `status` labels retained context `FRESH`/`STALE`; an
  unrelated Git diff is not an index-freshness signal.

## v0.1.3

- Added bounded, queryable source-evidence snapshot history and historical
  context retrieval by `context_hash`.
- Added a 128-record / 64 KiB local usage ring for meaningful architecture
  operations; it links outcomes to retained context without retaining prompts,
  queries, source paths, evidence text, commands, or diagnostic output.
- Stopped persisting graph/gate command and stdout tails in source-evidence
  snapshots, and bounded malformed MCP telemetry keys.

## v0.1.2

- Made `status` observational; telemetry is now a fixed-size aggregate without
  source, query, or task text.
- Stopped no-change watcher state rewrites and retained only the latest 32
  rebuildable evidence snapshots, while preserving last-good.
- Narrowed Codex guidance to architecture-relevant work so normal local tasks
  do not pay an architecture-context preflight.

## v0.1.1

- Generated Codex guidance now uses the portable `archctx` command and has a
  clear fallback when it is unavailable.
- Continuous watchers retain no-change heartbeats in local state without
  flooding stdout; `watch --once` retains its JSON result.

## v0.1.0

- Stable source-grounded architecture context coordinator for coding agents.
- Compact CLI/MCP context with revision-bound source evidence, authored
  relations, separate CALM code-edge facts, and stale/last-known-good status.
- Deterministic external-gate promotion, impact, retained evidence delta, and
  evidence watcher support.
- Windows and Linux test coverage, including installed CLI verification in CI.

### Boundaries

- Source code remains authoritative; architecture context is rebuildable.
- Authored relations and CALM code edges have separate provenance.
- A CALM full index is never reported as incremental.
