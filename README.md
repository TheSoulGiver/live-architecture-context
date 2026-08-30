# Live Architecture Context

Source-grounded architecture context for long-running coding agents.

This is intentionally a coordinator, not another parser or graph database:

`CodeGraphContext (code graph) → this tool (canonical/evidence/revision/last-good) → Archify (typed IR/rendering)`

It validates every configured canonical claim against source text, pins it to a Git revision, atomically promotes only passing snapshots, and otherwise serves the last-known-good context marked `STALE`.

## Quick start

```sh
python archctx.py --config example.archcontext.json refresh
python archctx.py --config example.archcontext.json canonical snapshot-store
python archctx.py --config example.archcontext.json trace cli
```

`code_graph.refresh` is an optional argv array. It is run without a shell and must succeed before promotion, so CGC (or another graph implementation) remains replaceable.

The `trace` and `impact` commands deliberately say `authored_architecture_*`: they do not misrepresent configured architecture relations as compiler-proven call edges. Ask the configured code-graph tool for code-level callers/callees.

## License

MIT. It contains no copied code from CGC or Archify.
