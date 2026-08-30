# Changelog

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
