# Live Architecture Context

`source → CALM code graph → compact canonical context → Archify gate/snapshot`.

This is not a parser, renderer, or second source of truth. It binds a small set
of repo-authored architecture claims to exact source evidence and a revision,
keeps only a passing last-known-good snapshot, and gives agents compact JSON.
CALM remains the owner of code edges; Archify remains the typed-IR/visual
validator. Their outputs are never relabelled as authored relations.

## Three-minute start

Windows PowerShell or Linux/macOS shell:

```sh
git clone <this-repository>
cd live-architecture-context
python -m unittest -v
python archctx.py --config demo-repo/architecture.json refresh
python archctx.py --config demo-repo/architecture.json canonical service
python archctx.py --config demo-repo/architecture.json impact --files src/service.py
```

The only runtime dependency is Python 3.10+. `pip install .` can expose the
same command as `archctx` where a normal Python build environment is present;
the source script is the offline/default route.

## Live loop

```sh
python archctx.py --config architecture.json refresh
python archctx.py --config architecture.json watch --poll-ms 500
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
{"command":"python","args":["/absolute/path/archctx.py","--config","/absolute/path/architecture.json","mcp"]}
```

`trace` without `--code` and `impact` are deliberately authored/evidence
results. With a configured `code_graph.query`, code edges appear in a separate
`code_graph` field with `confidence: provider_reported`.

`calm_query.py` is the supplied thin adapter for CALM's read-only MCP
`callers`/`callees` tools. It returns a capped, confidence-labelled edge list;
CALM remains the parser/index owner and a full CALM index is never labelled
incremental.

## Codex-native adoption

Keep a private, gitignored config at `.archctx/architecture.json`, then install one small
managed block into the repository's existing `AGENTS.md`:

```sh
python /stable/path/archctx.py --config .archctx/architecture.json refresh
python /stable/path/archctx.py --config .archctx/architecture.json install-codex
```

The block is placed at the top of `AGENTS.md`: it tells every new Codex session
to run `status`, use `search --query "<current task>"` before broad archaeology,
read only matching evidence/canonical components, and checkpoint with
`impact`/`refresh`.
`install-codex --check` previews this without writing. It is idempotent and
updates only the `<!-- archctx:* -->` block; a stale or unavailable index never
blocks normal development. The installer rejects a config outside the repo, so
an AGENTS block cannot accidentally depend on a private workstation path.

## Release boundaries

- MIT; no source is copied from CALM or Archify. See [NOTICE.md](NOTICE.md).
- Do not commit `.archctx`, repo-specific configs, source mirrors, or evidence
  from private repositories.
- Compatibility, threat model, and release checks are in [docs](docs).

## License

MIT.
