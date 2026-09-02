<!-- archctx:begin -->
## Architecture context

For each substantive task, before broad repository archaeology:

1. Run `<python> ../archctx.py --config architecture.json status` (`py -3` on Windows; `python3` on Linux/macOS).
2. Run `<python> ../archctx.py --config architecture.json search --query "<current task>"`; query `canonical` and `trace` only for matches.
3. Before an architecture-relevant edit, use `impact --files <changed paths>`; after the checkpoint run `refresh` and, when relevant, `changed-since`.

Treat `STALE` as last-known-good context, not current truth: inspect the cited source before relying on it. If archctx is unavailable or has no config, continue normal development and do not invent architecture facts. Authored traces are not CALM code edges; request code edges separately with `trace --code` when configured.
<!-- archctx:end -->
