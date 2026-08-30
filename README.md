# Live Architecture Context

`source → CALM code graph → compact canonical context → Archify gate/snapshot`.

This is not a parser, renderer, or second source of truth. It binds a small set
of repo-authored architecture claims to exact source evidence and a revision,
keeps only a passing last-known-good snapshot, and gives agents compact JSON.
CALM remains the owner of code edges; Archify remains the typed-IR/visual
validator. Their outputs are never relabelled as authored relations.

## Three-minute start

Use `py -3` on Windows and `python3` on Linux/macOS in place of `<python>`:

```sh
git clone <this-repository>
cd live-architecture-context
<python> -m unittest -v
<python> archctx.py --config demo-repo/architecture.json refresh
<python> archctx.py --config demo-repo/architecture.json canonical service
<python> archctx.py --config demo-repo/architecture.json impact --files src/service.py
```

The only runtime dependency is Python 3.10+. `pip install .` can expose the
same command as `archctx` where a normal Python build environment is present;
the source script is the offline/default route.

## v0.1 field evidence

The release was dogfooded against two private, long-lived repositories without
including their source or configuration here. One representative new-session
comparison used less input context and fewer file reads with archctx; it was a
single workflow sample, not a general speed or code-correctness benchmark.
Fresh sessions also used source evidence to correct a parallel implementation.
Those observations do not make this index authoritative over source or replace
repository-specific tests and review.

## Live loop

```sh
<python> archctx.py --config architecture.json refresh
<python> archctx.py --config architecture.json watch --poll-ms 500
```

The watcher hashes configured evidence plus optional `watch.paths`; an
unrelated watched file returns `NO_RELEVANT_CHANGE`. A changed canonical source
performs one validation/promotion cycle. Invalid evidence or a failed external
gate leaves `last-good.json` untouched and every response is `STALE`/`INVALID`.
`code_graph.incremental` can receive `{changed_files}`; for a persistent CALM
daemon without that command, the result explicitly says
`external_daemon_unverified`, rather than claiming a graph refresh occurred.

## Config contract (v1)

`example.archcontext.json` is the complete minimal form. Components declare
`truth_sources` for humans and required `evidence` (`path` + exact `contains`)
for machines. Relations must have an explicit `kind` and are returned with
`provenance: authored_architecture`. `gates` and optional CALM commands are
argv arrays, never shell strings.

```json
{"version":1,"repo":".","components":[{"id":"service","truth_sources":["src/service.py"],"evidence":[{"path":"src/service.py","contains":"def serve"}]}],"relations":[]}
```

Supported command substitutions are `{repo}`, `{state}`, `{changed_files}`;
code-graph queries also receive `{symbol}` and `{direction}`. Treat project
config as trusted code: it intentionally authorizes its argv programs.

## Agent protocol

Every CLI/MCP response includes `protocol_version`, `revision`, `freshness`,
`confidence`, source references where applicable, and `next_action`.

```sh
archctx --config architecture.json status
archctx --config architecture.json canonical service
archctx --config architecture.json evidence service
archctx --config architecture.json trace service --code
archctx --config architecture.json changed-since --revision <git-sha>
archctx --config architecture.json drift --base <git-sha>
archctx --config architecture.json mcp
```

MCP tools: `status`, `refresh`, `snapshot`, `canonical`, `evidence`, `trace`,
`impact`, `changed-since`, `drift`, and `stale` (all prefixed
`architecture_`). An MCP client configuration is simply:

```json
{"command":"/absolute/path/to/python","args":["/absolute/path/archctx.py","--config","/absolute/path/architecture.json","mcp"]}
```

`trace` without `--code` and `impact` are deliberately authored/evidence
results. With a configured `code_graph.query`, code edges appear in a separate
`code_graph` field with `confidence: provider_reported`.

`calm_query.py` is the supplied thin adapter for CALM's read-only MCP
`callers`/`callees` tools. It returns a capped, confidence-labelled edge list;
CALM remains the parser/index owner and a full CALM index is never labelled
incremental.

## Codex-native onboarding

Install the CLI once, then bind the first source-backed component. This is the
only architecture input required; ArchCtx does not infer a canonical system
from filenames or write architecture claims on its own.

```sh
python -m pip install live-architecture-context
cd your-repository
archctx init --component service --evidence 'src/service.py::def serve'
```

`init` creates the private `.archctx/architecture.json`, adds `.archctx/` to
`.gitignore`, refreshes a passing last-good snapshot, and places a compact
managed block in the existing `AGENTS.md`. It preserves the rest of that file.
Run it again after upgrades: it never overwrites the config or user rules.

Every new Codex session then performs a small `status` check before substantive
work. A fresh context narrows the first read through `search`; a stale one uses
last-good only as a direction to source verification; missing or failed context
falls back to normal development. It is an accelerator, not a gate.

```sh
archctx --config .archctx/architecture.json uninstall-codex
```

Uninstall removes only the managed block. Config, last-good history, and
`.gitignore` are deliberately preserved. `install-codex --check` still previews
an existing manual configuration without writing. A polling watcher remains an
optional faster maintenance path; the per-session revision/evidence check is
the default lazy maintenance path.

## Release boundaries

- MIT; no source is copied from CALM or Archify. See [NOTICE.md](NOTICE.md).
- Do not commit `.archctx`, repo-specific configs, source mirrors, or evidence
  from private repositories.
- Compatibility, threat model, and release checks are in [docs](docs).

## License

MIT.
