<!-- archctx:begin -->
## Architecture context

Use Archctx only when it shrinks the next broad source read (canonical/truth/evidence, cross-component path, freshness/delta, or legacy ambiguity); skip obvious local work.
Run `archctx --config architecture.json status`. Use its `FRESH`/`STALE` label, not unrelated Git dirtiness. If `FRESH`, use the smallest matching query: `search` to locate; `canonical`/`evidence` for a known component; `impact --files <paths>` before cross-component edits; `history` for prior context; `changed-since`/`drift` only with a supplied base revision.
Source wins; stale, missing, or irrelevant context means normal targeted discovery. Let the watcher refresh; use `refresh` only without one. Orientation, never a gate.
<!-- archctx:end -->
