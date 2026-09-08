# Live Architecture Context

**The small, source-grounded answer to: “what should this coding agent trust right now?”**

Live Architecture Context (LAC) gives a new coding-agent session a compact,
revision-bound starting point: the canonical implementation, the declared truth
source, exact source evidence, and whether that retained orientation is still
fresh. Source code stays authoritative. LAC is a rebuildable index, not a
second architecture database.

![A new Agent session searches broadly without LAC, but starts from canonical source evidence with LAC](assets/hero.svg)

## Measured context efficiency, not a token promise

One paired, fresh-session workflow on an anonymized long-lived repository used
**25% less input context** and **43% fewer distinct file-token reads** with
LAC. It is one observed real workflow, not a universal token, speed, cost, or
correctness benchmark.

![Observed real-workflow context comparison](assets/context-efficiency.svg)

The aggregate values, method, limitations, and deterministic SVG renderer are
checked in: [benchmark data](benchmarks/observed-context-ab.json) ·
[claim evidence](docs/public-face/claim-evidence.md) ·
`python tools/render_benchmark.py --check`.

## Install in 30 seconds

```sh
# Released package
python -m pip install "git+https://github.com/TheSoulGiver/live-architecture-context.git@v0.1.6"

# Or, from the current checkout (including unreleased changes)
python -m pip install .
cd your-repository
archctx init --component service --evidence 'src/service.py::def serve'
```

`init` creates private `.archctx/architecture.json`, records a passing
last-known-good snapshot, and adds a small managed instruction block to the
existing `AGENTS.md`. A fresh Codex session learns Archctx exists, but calls it
only when a system-level question can shrink the next source read. It never
invents a canonical system from filenames.

## Shared GitHub mode

GitHub is the default channel for reproducible code facts: reviewed source,
small completed commits, durable design docs, and a project-safe architecture
config/view. `init` intentionally keeps its default `.archctx/` configuration
private. When a team needs shared architecture context, keep the reviewed
config and optional Archify view in a dedicated tracked directory (for example
`architecture/architecture.json` and `architecture/architecture.view.json`)
and use `archctx --config architecture/architecture.json ...`. The
rebuildable state remains in that directory's adjacent `.archctx/` directory
and stays local: last-good, snapshots, watcher state, usage, and candidate
decisions are not GitHub facts. This repository follows that pattern in its
tracked [shared architecture config](architecture/architecture.json).

For a fresh worktree, follow [Project-local onboarding](docs/WORKTREE_ONBOARDING.md).
`--config` now also selects the configuration used by `init`; an existing
reviewed definition is preserved, not replaced with a private one. `init` is
an explicit setup operation: it updates the managed instruction block and
ignore rule, then runs refresh (including trusted configured commands).
Use `init --check` to preview without writes or command execution. If the
conventional shared config exists, omission of `--config` is an error, not
a silent choice between shared and private definitions. Legacy private-only
repositories retain their default.

`init` and `install-codex` accept `--command "python archctx.py"` (or your
project-local CLI prefix). This is instruction text only: the installer does
not execute it or change any installation. Explicit `--state-dir` is retained
in the managed guidance and must be inside the repository for onboarding.

Before a normal sync, fetch and compare the intended remote branch, inspect the
staged content for secrets and runtime data, then make a clear commit and only
fast-forward push. This is a convergence rhythm, not a development Gate:
dirty or unpushed work can continue. Runtime databases, credentials, user and
session data, live process state, generated artifacts, and raw local evidence
remain in their owning environment. A committed revision does not imply a
deployed revision is running.

## On-demand Codex skills

For this repository's working map, follow [Open the Living Blueprint](docs/LIVING_BLUEPRINT.md).
The tracked config/view reproduce a local, source-linked Archify diagram and
Before / Delta / After. The viewer follows the same accepted version as Agent
queries and displays stale results with their retained evidence.

The bundled `plugins/live-architecture-context/.codex-plugin/plugin.json`
points only at three small skills. It has no lifecycle hook and registers no
always-on MCP tool set:

- `architecture-context` for canonical/truth/evidence, unfamiliar cross-module
  systems, recent changes, and legacy ambiguity.
- `architecture-impact` for a boundary-crossing edit or diff.
- `architecture-recovery` for stale, invalid, missing, or contradicted context.

Each skill reuses the existing CLI/MCP and starts with one test: will this
remove the next broad source read? If not, it stays out of the way. The managed
`AGENTS.md` block is the same compact fallback for hosts without plugin skills.

For a local checkout, Codex can install the repo-local marketplace once, then
load the skills in a new session:

```sh
codex plugin marketplace add .
codex plugin add live-architecture-context@live-architecture-context
```

This optional plugin install is separate from `pip install`, which supplies the
`archctx` CLI.

## Why LAC exists

Repository search answers “where does this string occur?” A code graph answers
“how does this code connect?” A diagram answers “how is this architecture
explained or validated?” Those remain useful. None alone gives a new Agent a
small, source-evidence-bound answer to all of these at once:

- Which implementation is canonical, rather than merely reachable or similar?
- What source is the truth source for this claim?
- Is the remembered answer current, stale, or unavailable?
- If validation fails, can the Agent retain last-good direction without silently
  treating it as current?

![LAC connects code facts, Archify's authored architecture, and Agents](assets/trust-stack.svg)

> **Source is truth. LAC remembers what is canonical, and knows when that memory is stale.**

### Core primitives

| Primitive | Agent outcome |
| --- | --- |
| `status` / `stale` | A tiny freshness result, not a hidden full snapshot. |
| `canonical` / `evidence` | One declared implementation and its exact source proof. |
| `search` | At most three candidates by default, plus explicit omitted counts. |
| `trace` / `impact` | Authored relations (with optional source evidence) stay separate from graph facts. |
| `refresh` / `snapshot` | Validate before atomic promotion; preserve last-good on failure. |
| `changed-since` / `drift` / `candidates` | Evidence-bound delta, historical drift, and current high-value source candidates. |

## Different jobs, complementary tools

This is a scope comparison, not a feature ranking. Each project below is useful
for a different question; the linked primary sources and caveats are maintained
in [comparison sources](docs/public-face/comparison-sources.md).

| Start with | Primary job | What it gives an Agent | What LAC adds instead of duplicating it |
| --- | --- | --- | --- |
| Direct repository search | Read current source | Exact files and strings | A compact, declared canonical starting point with freshness state. |
| [CALM](https://github.com/Eilodon/CALM) | Optional code facts and dependency graph | Callers, callees, imports, graph/index freshness | A truth-source and canonicality contract; configured graph facts remain separately labelled. |
| [CodeGraphContext](https://github.com/CodeGraphContext/CodeGraphContext) | Researched graph/index alternative | Its own code-fact model | No runtime dependency or copied implementation. |
| [Archify](https://github.com/tt-a1i/archify) | Authored typed IR, validation, and human visualization | Validated diagrams, revision-pinned evidence when configured | Source-current/LKG context and a verified projection; Archify remains the visual and validation foundation. |
| [ArchContext](https://github.com/Ancienttwo/arch-context) / [GyroCompass](https://github.com/gyrocompass-io/gyrocompass) | Architecture control loops, rules, and drift policy | Workflow lifecycle, practices, or architecture rules | A smaller rebuildable index that does not own an authored architecture baseline. |
| **Live Architecture Context** | Canonical orientation | What implementation and truth source to trust now | Composes CALM code facts with Archify's typed visual layer without copying either owner. |

LAC does not relabel a graph edge as an authored relation, copy a parser or
renderer, or claim that a passing snapshot is source authority.

Lineage matters: an early prototype described CodeGraphContext in its concept
chain; the current optional graph adapter is CALM. ArchContext and GyroCompass
are comparison/research inputs, not embedded runtime dependencies.

## Real dogfood, anonymized

**Fresh session.** A new Codex session used compact canonical context to locate
the relevant source path, then read only returned evidence. In a later session,
that source evidence helped correct a parallel implementation path rather than
continuing to elaborate it.

**Stale session.** A canonical source changed. LAC kept the last-known-good
record, returned `STALE`, and directed the Agent to verify source before using
the old orientation. Only a passing `refresh` promoted a new `FRESH` record.

These observations came from two private, long-lived repositories. Their code,
paths, product details, configurations, and raw logs are deliberately absent
from this repository.

## Live loop

```sh
archctx --config architecture.json refresh
archctx --config architecture.json watch --apply --poll-ms 500
```

The watcher hashes configured evidence, optional `watch.paths`, the architecture
config, an optional Archify view, and configured high-value candidate paths.
`watch --once` returns `NO_RELEVANT_CHANGE` for an unrelated watched file, while
a continuous watcher is quiet and only updates its local manifest when it
changes. Default `watch` observes and marks stale. Opt-in `watch --apply`
promotes only an already-declared context after source validation, a configured
graph receipt (when one is available), gates, and optional Archify validation pass. It never
writes components or relations: a high-value candidate remains
`CANDIDATE_REVIEW_REQUIRED` until an Agent explicitly accepts or rejects it.
Invalid evidence or a failed external validator leaves both `last-good.json` and
the previous Archify output untouched.
`code_graph.incremental` can receive `{changed_files}`. An argv name or exit code
does not prove an incremental graph update: legacy commands are labelled
`unverified`. To bind a graph fact, opt into `receipt: "stdout_json_v1"`; the
provider must emit one JSON object with the validated `{context_hash}`,
`{revision}`, actual `mode`, a short `graph_revision`, and `fresh: true`
(plus `{changed_files_sha256}` and a count for an incremental request). For a
persistent CALM daemon without that receipt, the result explicitly says
`external_daemon_unverified`, rather than claiming a graph refresh occurred.
The watcher refuses scopes above 512 files or 8 MiB of configured source; narrow
`watch.paths` instead of turning each polling tick into a repository scan.

## Agent protocol

Every CLI/MCP response includes `protocol_version`, `revision`, `freshness`,
`confidence`, source references where applicable, and `next_action`.

```sh
archctx --config architecture.json status
archctx --config architecture.json history --limit 8
archctx --config architecture.json history --context-hash <context-hash>
archctx --config architecture.json usage --operation impact --limit 8
archctx --config architecture.json search "identity payment"
archctx --config architecture.json canonical service
archctx --config architecture.json evidence service
archctx --config architecture.json trace service --code
archctx --config architecture.json impact --files src/service.py
archctx --config architecture.json changed-since --revision <git-sha>
archctx --config architecture.json drift --base <git-sha>
archctx --config architecture.json candidates
archctx --config architecture.json candidates --limit 0
archctx --config architecture.json accept <candidate-id> --bind component:provider
archctx --config architecture.json reject <candidate-id> --reason false_match
archctx --config architecture.json mcp
```

`status` is metadata-only: it reports freshness and whether last-good exists
without serializing context. Use `snapshot` only when the complete retained
context is needed. `search` returns at most three matches by default and always
reports `match_count` and `omitted_match_count`; `search --query "…"` remains
compatible, but the positional form is shorter for Agents. Pass `--limit 0` only when an
unbounded result is genuinely needed.
`canonical` and `evidence` also accept `--component`; `trace --from <id> --to <id>` reports
authored reachability and whether that relation is direct.

When a stale/missing baseline or multiple installs make the selected context
ambiguous, use `archctx status --diagnose` (with your usual `--config` and optional
`--state-dir`). The same read-only response adds resolved config/state/last-good
paths, the state selection rule, core version, module/interpreter paths and the
core source SHA-256 captured at import. It works without a config and writes
nothing, including usage. Default `status` stays compact. MCP status/stale accept
`{"diagnose":true}`. Diagnostics contain private local paths; keep them local.
The core hash identifies that source file, not a Git revision, the other modules
or an optional graph/render provider. A package version alone is not proof that
two entrypoints run the same code. Retain the diagnostic with an existing task
summary when investigating adoption; do not start logging every status poll.

For a meaningful consumer problem or result, reuse the existing task summary:
link the task/change, `used`/`skipped`/`unavailable` decision, core identity and
context hash, and an existing usage receipt (time + operation). Separate the
Agent's claimed decision effect from a checkable result and measured overhead;
missing time/token observations are unknown, not zero. Ordinary skips need no
report. Exchange new findings at task boundaries using the host's existing
delivery mechanism, when available. A saved message is not proof it was read;
a published fix is not proof the consumer adopted it. Do not poll by repeatedly
starting models, or copy private diagnostics/evidence into a public issue.

In a repository initialized with the standard `.archctx/architecture.json`, the
CLI accepts `archctx status` (and the other non-`init` commands) without
`--config` when no conventional shared config is present; it never searches
elsewhere for a config. Explicit `--config`
remains the portable form for a nonstandard location.

MCP tools: `status`, `refresh`, `snapshot`, `history`, `usage`, `candidates`,
`accept_candidate`, `reject_candidate`, `canonical`, `evidence`, `trace`,
`impact`, `changed-since`, `drift`, and `stale` (all prefixed `architecture_`).
An MCP client configuration is simply:

```json
{"command":"/absolute/path/to/python","args":["/absolute/path/archctx.py","--config","/absolute/path/architecture.json","mcp"]}
```

`trace` without `--code` and `impact` are deliberately authored/evidence
results. With a configured `code_graph.query`, code edges appear in a separate
`code_graph` field with `confidence: provider_reported`.

`archctx-calm-query` is the supplied thin adapter for CALM's read-only
`callers`/`callees` tools. It attaches to an explicitly managed loopback
Streamable HTTP endpoint; it starts no daemon, uses no `npx`, and stores no
CALM response. Each query orients with `repo_overview`, rejects a non-ready or
non-fresh CALM watcher, then returns a capped, confidence-labelled edge list.

```json
{"code_graph":{"provider":"CALM","query":["archctx-calm-query","--repo","{repo}","--symbol","{symbol}","--direction","{direction}","--endpoint","http://127.0.0.1:<port>/mcp"]}}
```

CALM remains the parser/index owner. Its current status protocol does not
publish the exact processed-path digest for one watcher transaction, so this
adapter deliberately cannot emit a verified incremental refresh receipt. Its
edges remain separately labelled `provider_reported`; source evidence and
last-known-good promotion remain Archctx's own fail-closed contract.

## Config contract (v1)

`example.archcontext.json` is the complete minimal form. Components declare
`truth_sources` for humans and required `evidence` (`path` + exact `contains`)
for machines. Relations must have an explicit `kind`; they may carry the same
evidence form, which is validated and returned by `trace`. A relation without
it remains explicitly `authored_architecture`, not a claimed code-graph fact.
`gates` and optional CALM commands are argv arrays, never shell strings.

```json
{"version":1,"repo":".","components":[{"id":"service","truth_sources":["src/service.py"],"evidence":[{"path":"src/service.py","contains":"def serve"}]}],"relations":[]}
```

## Archify projection

`archctx-to-archify` is a thin, installed projection from a
declared Archctx config plus a human-authored view to Archify architecture IR.
The CLI revalidates source evidence before it writes output. It never parses
source, changes canonical state, or invents a relation: every visible node and
connection must exist in the config. It maps arbitrary Archctx IDs injectively
into Archify-safe IDs, and an explicit view selects a relation only by its
canonical ID. The prototype deliberately does not pass through arbitrary
Archify cards, boundaries, or routing fields.

```sh
archctx-to-archify --config demo-repo/architecture.json --view demo-repo/architecture.view.json --output /tmp/architecture.json
archify validate architecture /tmp/architecture.json --quality showcase --json
```

To keep a human Archify blueprint alongside the opt-in live loop, declare the
view, derived output, and a pinned Archify validation command in the same trusted
config:

```json
{"archify":{"view":".archctx/architecture.view.json","output":".archctx/architecture.archify.json","validate":["archify","validate","architecture","{archify_output}","--quality","showcase","--json"]}}
```

`watch --apply` stages derived output in an immutable local generation, validates
it, and atomically promotes the matching Archctx record only after rechecking
source/config/view inputs. A process-scoped writer lock serializes promotion.
The configured loose output remains a compatibility copy; accepted context
references the complete generation. Optional `render` and `compare` argv reuse
Archify's real delivery/comparison receipts to bind the HTML to the same IR.
The view remains human-authored and may intentionally be focused; Archctx never
fills in omitted nodes or invents a relation.

Supported command substitutions are `{repo}`, `{state}`, `{changed_files}`;
code-graph queries also receive `{symbol}` and `{direction}`. Treat project
config as trusted code: it intentionally authorizes its argv programs.

`archctx --config .archctx/architecture.json uninstall-codex` removes only the
managed `AGENTS.md` block. Config, last-good history, and `.gitignore` stay in
place. `history` lists the latest 32 source-evidence snapshots and retrieves a
specific historical typed context by `context_hash`; it never copies that
context into another store. `usage` keeps at most 128 local operation receipts
or 64 KiB, whichever is smaller, for meaningful architecture operations. A
receipt links to its retained context hash and component IDs, but never stores a
prompt, query, source path, evidence text, command, stdout, or stderr. `status`,
`history`, `usage`, and idle watcher ticks are read-only. Existing legacy
`telemetry.jsonl` can be compacted once with `usage --import-legacy`; it is not
appended. CLI/MCP telemetry remains a fixed-size aggregate. The latest 32
source-evidence snapshots are retained for delta queries; `last-good.json` is
always retained. Persisted snapshots retain validator receipts, not diagnostic
command/output tails. Telemetry also reports bounded result categories and an
`actionable_result_rate`; it is a result proxy, not evidence that an Agent used
the result or that a task succeeded.

Candidate baselines contain only repo-relative paths, counts, line numbers, and
hashes; they never retain matched source text or a patch. Candidate decisions
are capped at 64 local records / 64 KiB and retain fixed reason codes or
component/relation bindings, not prompts or source content. A one-time
candidate-baseline migration requires the explicit
`refresh --reset-candidate-baseline` flag and is marked in snapshot history.
Candidate rules are intentionally bounded to 256 files / 4 MiB of source per
observation and respect `watch.ignore`; broad rules fail closed as stale rather
than turning the watcher into a full-repository scanner. The independent watcher
scope is likewise capped at 512 files / 8 MiB.

## Boundaries and release notes

- MIT; no source is copied from CALM, Archify, or the tools above. See [NOTICE.md](NOTICE.md).
- Do not commit `.archctx`, repo-specific configs, source mirrors, or evidence from private repositories.
- Compatibility, threat model, and release checks are in [docs](docs).

## License

MIT.
