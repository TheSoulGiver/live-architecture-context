# Release checklist

1. Run `<python> -m unittest -v`, then install the wheel into a clean target and run both `archctx` and `archctx-to-archify` against the synthetic demo (`py -3` on Windows; `python3` on Linux/macOS). Include one `watch --once --apply` check with a configured validator.
2. Run `git diff --check`; inspect `git status --short` for private state.
3. Confirm README examples work from a clean clone on Windows and Linux CI.
4. Pin CALM/Archify commands in consumer configs; do not claim their outputs
   are authored facts.
5. Confirm package metadata, all installed CLI names, `archctx.py` MCP
   `serverInfo.version`, plugin manifest, CHANGELOG, README, NOTICE, and
   LICENSE agree on the version and boundaries.
6. Create an annotated `v<version>` tag, push `main` and the tag, then publish a
   GitHub release with the checked revision.
