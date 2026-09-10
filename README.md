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

## Start with the system map

```sh
# From this checkout, including the native commands below
python -m pip install .
cd your-repository
archctx setup
archctx understand "How does this request reach storage?" --files src/service.py src/storage.py
archctx map
```

Use Python 3.10+, Git and Node.js 20+. `setup` is the explicit one-time network
operation: LAC installs its compatible source-understanding and Archify
components in project-local ignored state. It preserves reviewed declarations;
on a new project it prepares an empty definition and view, without inferring
canonical components. No API key or separate model service is configured.

`understand` captures the selected saved source and performs mechanical
extraction. When it returns `NEEDS_AGENT`, the current authorized Agent reads
the returned compact contract and writes results to the returned paths, then
runs `archctx understand --resume <analysis_id>`. LAC handles merge, validation
and import. The Agent reviews useful findings and updates the shared definition
through existing acceptance. [Source understanding](docs/UNDERSTAND.md) explains
this boundary. Ordinary queries and map polling never invoke a model.

`map` opens the local live system map. People and Agents use the same component
IDs, accepted source evidence and declared change scope. Project, component,
flow and change views distinguish confirmed architecture, discovered findings
and changed source. The map keeps working during development and explicit
acceptance. [Map guide](docs/LIVING_BLUEPRINT.md).

These commands describe the current checkout, not the earlier `v0.1.6` release.
The previous `init --component ... --evidence ...` onboarding and expert
entrypoints remain available for existing integrations.

## Shared GitHub mode

GitHub is the default channel for reproducible code facts: reviewed source,
small completed commits, durable design docs, and a project-safe architecture
config/view. `init` intentionally keeps its default `.archctx/` configuration
private. When a team needs shared architecture context, keep the reviewed
config and optional Archify view in a dedicated tracked directory (for example
`architecture/architecture.json` and `architecture/architecture.view.json`)
and use `archctx --config architecture/architecture.json ...` when an explicit
selection is useful. The
rebuildable state remains in that directory's adjacent `.archctx/` directory
and stays local: last-good, snapshots, watcher state, usage, and candidate
decisions are not GitHub facts. This repository follows that pattern in its
tracked [shared architecture config](architecture/architecture.json).

For a fresh worktree, follow [Project-local onboarding](docs/WORKTREE_ONBOARDING.md).
`--config` now also selects the configuration used by `init`; an existing
reviewed definition is preserved, not replaced with a private one. `init` is
an explicit setup operation: it updates the managed instruction block and
ignore rule, then runs refresh (including trusted configured commands).
Use `init --check` to preview without writes or command execution. The native
`setup`, `understand` and `map` commands can select the sole conventional
definition at `architecture/architecture.json` or `.archctx/architecture.json`.
If both exist, select one explicitly; they do not guess or scan other locations.
Existing query/onboarding commands preserve their selection contract: a shared
config requires explicit `--config`, and private-only repositories retain their
default. Keep one explicit prefix when moving between these operations.

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

Native [source understanding](docs/UNDERSTAND.md) keeps raw edge meaning and
provider provenance while the existing Agent supplies source-grounded semantic
judgment. Findings have stable IDs plus separate content and evidence revisions;
review bindings connect them to canonical component IDs. A file or understanding
group is never automatically a canonical component. Changed source invalidates
relevant review bindings, while unchanged results can be reused at the next
meaningful task boundary. This is optional understanding, not another Gate.

For this repository's [system map](docs/LIVING_BLUEPRINT.md), run
`python archctx.py --config architecture/architecture.json map` after `setup`. The page
shows the accepted system plus saved worktree changes, without rendering on
each source save. Codex maintains changed architecture declarations; validation
then publishes one shared version for the map and Agent queries. Before / Delta
/ After and retained evidence remain available when a new update fails.

The bundled `plugins/live-architecture-context/.codex-plugin/plugin.json`
points only at three small skills. It has no lifecycle hook and registers no
always-on MCP tool set. This source checkout separately offers opt-in,
[trusted Codex hooks](docs/LIVING_BLUEPRINT.md#consume-changes-during-development)
using the same read-only `updates --since <cursor>` API:

- `architecture-context` for canonical/truth/evidence, unfamiliar cross-module
  systems, recent changes, and legacy ambiguity.
- `architecture-impact` for a boundary-crossing edit or diff.
- `architecture-recovery` for stale, invalid, missing, or contradicted context.

Each skill reuses the existing CLI/MCP and starts with one test: will this
remove the next broad source read? If not, it stays out of the way. The managed
`AGENTS.md` block is the same compact fallback for hosts without plugin skills.

### Optional reuse decision with Ponytail

At [Ponytail's existing-implementation step](https://github.com/DietrichGebert/ponytail/blob/356918eba965ee1eac64bd3a7f0dd02108350de5/skills/ponytail/SKILL.md),
the existing `architecture-context` skill can supply a small owner/reuse/impact
lookup when that answer is uncertain. Reuse valid context already in the task,
verify the relevant source and wiring, then choose the smallest correct change.
Known local work adds no query. Correctness, readability and required invariants
outrank shortest diff, fewest files or fixed test counts; understanding a path
doesn't mean reading every file in full.

This is optional guidance, not a dependency, fork, hook, or reciprocal skill
invocation. Both tools work independently. The referenced upstream manifest
declares lifecycle hooks; that does not prove any consumer has loaded or run
them. Check the actual local source rather than identifying a same-named plugin
from a remote version alone. The skills add no hook or repeated rule injection;
the optional checkout hooks offer only changed context, never Ponytail's rules.

One-time readiness and per-task usage are different decisions. Restore a chosen
project-local tool/config at an authorized normal boundary using the existing
[onboarding path](docs/WORKTREE_ONBOARDING.md); a known-path task can still skip
LAC. Don't turn that skip into a forced trial or automatically defer readiness.

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
| `setup` / `understand` / `map` | Project-local readiness, bounded source understanding with the current Agent, and the shared human map. |
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
archctx --config architecture.json impact --files src/service.py --details
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
report. For a relevant completed task, an optional single line is enough:
`LAC: used/skipped/unavailable; decision effect; evidence reference; overhead measured/unknown`.
Only name Ponytail when it was involved. Reference the existing task change and
bounded receipt; don't generate another query merely to fill this line.
Distinguish Agent-reported influence from a checked change and net benefit;
an installed skill, running watcher or maintainer probe proves none of those.
Exchange new findings at task boundaries using the host's existing
delivery mechanism, when available. A saved message is not proof it was read;
a published fix is not proof the consumer adopted it. Do not poll by repeatedly
starting models, or copy private diagnostics/evidence into a public issue.

For existing query commands, `archctx status` without `--config` selects the
standard `.archctx/architecture.json` only when no conventional shared config
exists. Use an explicit config for shared definitions or a nonstandard location.
The native `setup` / `understand` / `map` entrypoints additionally discover a
sole conventional shared config; ambiguity still requires explicit selection.

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

`impact.change_scope` is the same contract used by the Live Development Map:
direct evidence owners, their declared `dependencies`, and their `dependents`
(objects to check, not a prediction of runtime breakage). For A calls B and B
calls C, editing B gives direct B, dependent A and dependency C. Cycles may put
an indirect component in both lists. Unknown files remain `uncovered_files`.
Each scope keeps raw relation IDs/direction/kinds and source evidence witnesses.
Only exact `calls`, `uses`, and `depends-on` default to a from-to dependency;
other kinds do not propagate unless a reviewed relation explicitly supplies
`"dependency": "from_to"` or `"to_from"`. `"none"` overrides even a default kind.
This is an authored interpretation, never a runtime or provider inference.

Accepted and working definitions are computed separately, bound to the accepted
context/revision and working config hash. `working: null` means unchanged
declarations, not fresh source; invalid working definitions have an explicit
error. Selecting the config path seeds changed components and the old/new
endpoints of changed relations in their respective graphs. For file renames,
pass both names; `--base` uses Git's no-renames file list. `--base` selects files,
not an otherwise unavailable historical graph.

Default scopes cap component/file lists at 12, relation witnesses at 8, evidence
at one anchor per relation, and the scope envelope at 8 KiB; `omitted` and
`omitted_evidence` make missing detail explicit. Use the same `impact --details`
(MCP `architecture_impact` with `details: true`) for full scope/evidence and
compare its context/config hashes. `trace` remains an explicit raw-direction
relation query. Compatibility fields retain their old meaning: CLI
`reachable_components` is all-kind outgoing reach, while the observer's
`impacted_components` is all-kind incoming reach. Neither is the new dependency
answer; both are labelled `legacy_semantics` and the page uses `change_scope`.

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
Native renderer commands use `{python} {lac_runtime}` to select the current core's adjacent runtime and preserve an isolated caller's `-I` mode.

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
