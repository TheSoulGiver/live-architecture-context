# Open and update this repository's blueprint

From this checkout, with Python 3.10+, Git and Node.js 18+:

```sh
python tools/archify.py setup
python archctx.py --config architecture/architecture.json refresh
python archctx_blueprint.py
```

The first command installs the reviewed Archify commit
`5de7275fe87a66a19d52a4d9b0b3a4f2a5a90115` (2.16.0) inside ignored local
state. An existing clean checkout at that revision can be reused with
`ARCHIFY_HOME`. Nothing is downloaded by refresh or the viewer. `setup`
does not overwrite an existing installation.

The viewer opens an unused loopback port. It shows the accepted architecture,
source evidence, coverage, current freshness, and Archify's actual
Before / Delta / After artifact. Keep this in another terminal while developing:

```sh
python archctx.py --config architecture/architecture.json watch --apply --poll-ms 1000
```

`archctx-blueprint` is also installed by `python -m pip install .`.
Use `--no-open --port 0` for an unattended local viewer. Stop each process with
Ctrl+C. They are developer tools, not system services. The viewer has no write
API, no access logs, and only serves accepted artifacts and matching source
evidence. When a file no longer matches its accepted hash, its evidence link
asks the reader to verify the worktree. GitHub source links appear in the
Archify artifact only when those evidence bytes match the stated commit.

## How Codex maintains the map

The shared config and view are reviewed declarations maintained by the Agent
doing the change. Their exact source anchors are deterministic observations;
their canonical ownership and relation meanings are design declarations.
They do not prove runtime reachability. Coverage and missing capabilities are
shown in both compact context and the viewer.

For a relevant edit, use `impact --files <paths>` to find the affected owners.
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
Every candidate must be decided before promotion. The watcher only promotes
already-declared, validated changes; it does not invoke a model or silently
invent canonical structure. Fresh sessions read `AGENTS.md` and the tracked
config, then rebuild their own local index. No previous chat is required.

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
their semantic diff.

## Code graph availability

This shared config does not assume a CALM daemon exists on another machine.
The viewer and Agent response say when code facts are disconnected. To use
CALM callers/callees, configure the existing `archctx-calm-query` adapter for a
locally managed endpoint. Its provider freshness/confidence remains separate
from authored relations. The current provider protocol cannot produce the
strict processed-content receipt required for a verified graph refresh.
