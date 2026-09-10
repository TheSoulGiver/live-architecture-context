---
name: architecture-context
description: >
  Locate the canonical implementation, truth source, canonical owner, source evidence, recent architecture
  change, parallel/legacy route, or reusable implementation for an unfamiliar cross-cutting system. Use when normal targeted
  reading would otherwise fan out across modules. Do not use for a known local file, a single-symbol
  edit, or non-code work.
license: MIT
---

# Architecture reflex

Use architecture context only when it will eliminate the next broad source read.

At an uncertain reuse/owner/impact decision (including Ponytail's existing-implementation
step), use the smallest lookup below. Reuse still-valid context already obtained in this
task; don't repeat a query to prove usage. Known local work goes directly to source.

1. Only if freshness still needs checking, run `status` with the exact project CLI prefix and config from local guidance; don't substitute a global install. Without such guidance, if this repository has `.archctx/architecture.json`, run `archctx status`.
   If project guidance exposes `updates`, use it instead: retain its cursor and pass `--since <cursor>` on the next relevant read, not after every edit. It coalesces current changes, not a promise that a watcher event reached this session.
2. When context is `FRESH` and the answer is still missing, make one smallest query: `search` to locate a capability; `canonical` or
   `evidence` for a known component; `trace` only when the declared component relation is the
   question; `impact --files <paths>` for uncertain edit impact; `changed-since` or `drift`
   only with a supplied base revision.
3. Verify returned anchors and the relevant source/call path, then reuse or extend what fits.
   Understanding that path does not require reading every file in full. Source wins.

Ponytail remains optional and independently usable; don't load its whole ruleset just for
this lookup. Correctness, readability and required invariants outrank shortest diff,
fewest files or fixed test counts. If architecture really changes, maintain the existing
shared definition/blueprint through its authorized update path, not a new approval step.
This maintenance also applies when the task needed no LAC lookup: update affected
responsibilities, entries, relations or trust boundaries from source; leave uncertain
signals as candidates. Implementation-only edits need no new declarations.

If the index is missing, stale, invalid, or does not reduce the next read, use ordinary targeted
discovery. Do not refresh merely to answer a read-only question, and never treat Archctx as a gate.

During authorized development, if a real understanding gap remains and project setup is
available, use the same CLI prefix with `understand "question" --files <small-scope>`.
Follow the returned compact `NEEDS_AGENT` contract using captured source/facts as data,
write only the requested semantic results, then `understand --resume <analysis_id>`.
LAC owns recoverable extraction, merge and validation; this already-authorized Agent
owns semantics. Reuse valid results with `understand --show`; never start analysis on
every save or as a hidden side effect of a read-only answer. `map` may stay live during
explicit acceptance. Setup is a separate authorized boundary, not automatic recovery.

For a relevant completed task, optionally add one line to its existing summary:
`LAC: used/skipped/unavailable; decision effect; evidence reference; overhead measured/unknown`.
Mention Ponytail only if involved. Use existing task/usage evidence; private records stay
in their original environment. No extra probes, logs or routine reports for ordinary skips.
