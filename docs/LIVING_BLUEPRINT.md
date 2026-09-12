# Open and update this repository's system map

From this checkout, with Python 3.10+, Git and Node.js 20+:

```sh
python archctx.py --config architecture/architecture.json setup
python archctx.py --config architecture/architecture.json map --ensure
```

`setup` explicitly provisions the compatible source-understanding and Archify
components in ignored project-local state. Existing reviewed definitions and
incompatible or modified installations are preserved. Nothing is downloaded
by ordinary queries, refresh or the viewer. Provider versions and overrides
are diagnostics; [source understanding](UNDERSTAND.md) uses the same setup.

Run `setup` once; thereafter `map --ensure` is the development entrypoint, with live
observation enabled by default. It starts or reuses a matching background viewer
on an unused loopback port and returns its URL. The viewer owns only its
project-local observer. If no last-good exists, it requests the existing
refresh transaction to build the first accepted blueprint. Failed validation
does not create a pretend baseline. An existing last-good remains readable.

The page opens on the project overview and accepted Archify diagram with a
separate saved-worktree overlay. Its **Project / 项目**, **Components / 组件**,
**Flows / 流程** and **Changes / 变化** navigation uses the same accepted component
and relation IDs as Agent queries. Select a component for responsibilities,
related discoveries, source evidence and exact CLI commands for this config/state.
Confirmed components, discoveries awaiting review and changed source are labelled
separately. Source-understanding freshness never substitutes for accepted-source
freshness; provider provenance stays in expandable diagnostics.

Flows retain declared relation direction and dependency semantics; the discovered
source tour is explicitly a separate investigation aid. **Focus neighbors / 聚焦邻居**
reduces clutter. Changes shows saved worktree changes and the latest retained
accepted delta; **Before / After** opens Archify's real comparison. Selection,
expanded component evidence and camera position are retained where their
identities still exist.

Saved implementation changes highlight affected components without rendering or
inventing architecture nodes. Unmapped files remain explicitly uncovered.
After Codex maintains the shared config/view, settled definition changes request
existing validation and publication; the page switches to the accepted bundle
automatically. Invalid intermediate saves and failed refreshes retain the old
map with specific reasons. The development observation has its own identity;
its file activity is neither accepted architecture, task progress nor runtime.

Use `archctx map --ensure --no-open` for silent Agent startup/reuse, or add
`--read-only` for observation without automatic publication. `map --status`
checks the receipt against the live loopback identity without writing state.
The runtime code, accepted architecture, and source-analysis inputs have separate
identities: a running server does not prove a fresh diagram or fresh analysis.

Plain `archctx map --no-open --port 0` still runs in the foreground. The older
`archctx-blueprint` / `python archctx_blueprint.py` entrypoints remain compatible;
their explicit `--live` flag retains its original meaning, and omission provides
observation without automatic publication. Ctrl+C stops this viewer and its own
observer, not other sessions or services. No second watch terminal is needed;
the standalone `archctx.py ... watch` remains available for CLI-only use.
The HTTP page has no write API or access logs. Accepted evidence and explicitly
observed working-source links have separate identity checks. When a file no
longer matches its accepted hash, its accepted evidence link
asks the reader to verify the worktree. GitHub source links appear in the
Archify artifact only when those evidence bytes match the stated commit.

## How Codex maintains the map

The shared config and view are reviewed declarations maintained by the Agent
doing the change. Their exact source anchors are deterministic observations;
their canonical ownership and relation meanings are design declarations.
They do not prove runtime reachability. Coverage and missing capabilities are
shown in both compact context and the viewer.

For a relevant edit, use `impact --files <paths>` to find the direct owners,
declared dependencies and dependents in `change_scope`, shared with the page.
The map overlays only the accepted scope; unaccepted working definitions stay
separate. Raw relation direction/kind is not necessarily a call or dependency.
Unknown kinds do not propagate; stale evidence and uncovered files remain
explicit limitations. Expand the per-file relationship details or repeat the
same CLI query with `--details` (MCP `details: true`) when witnesses are omitted.
The existing `trace` command still exposes raw incoming/outgoing relations.
This repository explicitly declares call/read/write dependency directions where
source supports them, leaves the projection-to-viewer delivery relation
unclassified, and marks skill guidance as non-propagating. These declarations
are review scope, not a complete execution graph.
The bounded watcher discovers changed evidence, config/view, and configured
boundary signals. An implementation-only edit can refresh evidence without
adding components or relations. A high-value match produces a candidate; it
is not an automatic architectural conclusion.

```sh
python archctx.py --config architecture/architecture.json candidates
# Read the returned source; update config/view if the change is architectural.
python archctx.py --config architecture/architecture.json accept <id> --bind component:<owner>
# Or reject an actual false signal with a fixed reason:
python archctx.py --config architecture/architecture.json reject <id> --reason not_architecture
```

Codex supplies the semantic judgment and edits the tracked declarations.
Deterministic drift candidates must be decided before promotion. Native
[source-understanding findings](UNDERSTAND.md) are separate, non-blocking leads:
the component panel joins findings through real component IDs and shows their
stable finding IDs, content/evidence revisions, current review bindings and
historical source links without inserting raw analysis groups into the accepted
diagram. Use `understand "question" --files ...` at a meaningful development
boundary; follow its compact Agent contract and resume with the returned ID.
The live map remains open during this work and explicit acceptance. Publication
rechecks actual input hashes and proof under the shared writer lock; a busy
writer returns a retry without changing another session's observer.
The watcher only promotes
already-declared, validated changes; it does not invoke a model or silently
invent canonical structure. Fresh sessions read `AGENTS.md` and the tracked
config, then rebuild their own local index. No previous chat is required.
The live observer reuses `watch_once`, `updates` and the existing refresh
transaction. Idle polls use bounded metadata checks; continuous saves coalesce.
There is no new event ledger, model-per-save analysis or render-per-save loop.

### Silent recovery and load tradeoffs

At a relevant authorized development start, the managed Agent guidance uses
`map --ensure --no-open` once. It is not a command the user must remember on every
task, nor a hook on every query/tool call. CLI/MCP context queries require no
viewer. Closed terminals and lost processes do not erase the retained LKG,
analysis or history; the next ensure can start a new process using that state.
It does not install components, clear state or run semantic analysis.

| Choice | Current implementation / boundary |
| --- | --- |
| Startup and reuse | Same interpreter/entry, config/state, mode and loaded-code identity; loopback nonce handshake and OS locks prevent duplicate managed instances. |
| Another live version or mode | `MISMATCH`, preserving the existing process. No PID-only reuse, automatic killing or second writer. The owner must resolve it; ordinary development can continue. |
| Idle work | Existing bounded observer backs off from 0.75 to at most 3 seconds; detected activity resets it. Pending settle/retry deadlines retain their scheduling. The first save after idle can take about 3 seconds plus scan time to appear. |
| Local records | One overwritten runtime receipt, bounded startup failure details, no stdout/access-log stream; existing LKG/history retention is unchanged. |
| Host automation | No new service, login task or global hook. The existing read-only advisory hook is unchanged. A stopped machine has no listener; recovery occurs at the next authorized ensure. |

Only ensure-managed instances participate in reuse; older foreground viewers
are not adopted or stopped. Failures after the child acquires its owner lock
can retain a bounded startup reason in the same receipt. Failure before the
entrypoint loads may provide only an exit code; no diagnostic detail is invented.

Implementation uses stdlib [subprocess](https://docs.python.org/3/library/subprocess.html#subprocess.Popen):
Windows `CREATE_NO_WINDOW`, Unix `start_new_session`, no shell, no unused pipes.
Windows [job-object policy](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
can still terminate descendants when a host closes; no breakaway privilege or
survival across reboot is claimed. Recovery is repeatable, not a hidden system service.

Codex supports [SessionStart command hooks](https://learn.chatgpt.com/docs/hooks),
but exact hook definitions require host trust, and asynchronous hooks are tied
to their session. Adding an untrusted hook file would not prove automatic startup.
System autostart, a new file-watcher dependency and layout-only validation reuse
are deferred: the current gap is safe start/reuse, not a new scheduling platform
or weaker publication checks.

## Consume changes during development

At the next relevant development read, `updates` replaces a separate status check:

```sh
python archctx.py --config architecture/architecture.json updates
python archctx.py --config architecture/architecture.json updates --since <returned-cursor>
```

The caller keeps the cursor, including across task boundaries when useful. The
response coalesces current source/candidate changes and accepted deltas; repeated
reads of the same state stay compact. Expired snapshot baselines are explicit.
The CLI/MCP needs no server-side session state and never renders or refreshes.
Watcher output alone is not proof an Agent consumed a change.

This checkout also offers [native Codex hooks](../.codex/hooks.json): review them
with `/hooks` and trust the exact definitions to enable advisory context at
session/prompt boundaries and after `apply_patch`. The small adapter calls the
same `updates` path, retaining only bounded, local **offered** cursors, not
prompts, tool results, an event queue, or proof of reading. It starts no models
and changes no architecture. Hooks skipped by trust, input limits, or host tool
coverage leave the next-read path available; shell edits are caught there or at
the next prompt, not by a pretend all-tool hook. These are synchronous bounded
reads (five-second host timeout), not background hooks; they never reject a
tool call or extend a turn. Global settings are untouched.
Agents maintain semantic changes at completion even if they did not need a
lookup before editing. The packaged skills remain usable without these hooks.
Starting the live page does not enable or fix native hook delivery; next-read
`updates` remains the independent, portable Agent path.

## One accepted version

A refresh holds a process-scoped writer lock, validates source/config/view,
builds a unique local generation, and uses real Archify validation, delivery
and comparison. Publication rechecks the inputs and commits a single
last-good record that references the complete generation. Each receipt binds
context, source revision, IR, HTML and comparison hashes.

Changes during validation return a retry; an interrupted writer releases its
OS lock. An invalid candidate, renderer failure or interrupted publication
leaves the previous accepted bundle available. The configured loose IR output
is a compatibility copy; the viewer follows only the last-good generation.
The viewer checks source freshness and clearly marks a retained older view.

The delta distinguishes architecture definitions, presentation changes, and
source-evidence changes. The first render has no earlier accepted diagram;
its comparison is explicitly an initial baseline. Archify compares authored
IR; its `authored` proof level is not relabelled as runtime or code-graph proof.

Four local visual generations (at most 8 MiB each) and 32 compact context
snapshots are retained. Missing old IR is labelled as an unavailable Before,
not silently presented as an empty architectural diff; current rendering can
still recover from source.
Old visual bundles can expire while their compact historical context remains
queryable. Generations, renderer installation, usage and watcher state stay
under ignored `architecture/.archctx/`. Git stores source, config/view and
their semantic diff. Keep local browser screenshots/recordings there too;
they are observation evidence, not committed architecture or consumer results.

## What we borrowed from Understand Anything

Design only, from MIT-licensed version `5feed1f2ce4f9c368d860f4c0ebc36d98a4693fc`:
[layer/focus views and cheap overlays on positioned nodes](https://github.com/Egonex-AI/Understand-Anything/blob/5feed1f2ce4f9c368d860f4c0ebc36d98a4693fc/understand-anything-plugin/packages/dashboard/src/components/GraphView.tsx#L970).
We retain Archify rendering and LAC's existing state; no code or mandatory
dependency was copied. Its [incremental preparation](https://github.com/Egonex-AI/Understand-Anything/blob/5feed1f2ce4f9c368d860f4c0ebc36d98a4693fc/understand-anything-plugin/skills/understand/prepare-incremental.mjs#L476)
requires committed source, so it cannot replace saved-worktree observation.

A bounded comparison used only `archctx_blueprint.py` at `a8923bb` and two
in-memory fixtures. Python stdlib AST supplied structural shapes to the actual
upstream [fingerprint comparison](https://github.com/Egonex-AI/Understand-Anything/blob/5feed1f2ce4f9c368d860f4c0ebc36d98a4693fc/understand-anything-plugin/packages/core/src/fingerprint.ts#L142)
and update classifier, not its Tree-sitter end-to-end pipeline. An error-message
edit became `COSMETIC / SKIP`; an optional parameter became
`STRUCTURAL / FULL_UPDATE` because one file exceeded 50% of the one-file scope.
Existing LAC ownership/candidate logic mapped both edits to `blueprint-viewer`
without inventing candidates. Thus file activity stays visible even when no
architecture declaration changes. These fixtures demonstrate classification,
not natural consumer adoption or measured token savings.

## Code graph availability

This shared config does not assume a CALM daemon exists on another machine.
The viewer and Agent response say when code facts are disconnected. To use
CALM callers/callees, configure the existing `archctx-calm-query` adapter for a
locally managed endpoint. Its provider freshness/confidence remains separate
from authored relations. The current provider protocol cannot produce the
strict processed-content receipt required for a verified graph refresh.
