---
name: architecture-context
description: >
  Locate the canonical implementation, truth source, canonical owner, source evidence, recent architecture
  change, or parallel/legacy route for an unfamiliar cross-cutting system. Use when normal targeted
  reading would otherwise fan out across modules. Do not use for a known local file, a single-symbol
  edit, or non-code work.
license: MIT
---

# Architecture reflex

Use architecture context only when it will eliminate the next broad source read.

1. If this repository has an Archctx command/config, run its small `status` query.
2. When it is `FRESH`, make one smallest query: `search` to locate a capability; `canonical` or
   `evidence` for a known component; `trace` only when the declared component relation is the
   question; `changed-since` or `drift` only with a supplied base revision.
3. Read only the returned source evidence needed for the task. Source wins over the index.

If the index is missing, stale, invalid, or does not reduce the next read, use ordinary targeted
discovery. Do not refresh merely to answer a read-only question, and never treat Archctx as a gate.
