<!-- archctx:begin -->
## Architecture context

Use Archctx only when it shrinks the next broad source read (canonical/truth/evidence, cross-component path, freshness/delta, or legacy ambiguity); skip obvious local work.
From the repository root, run `python archctx.py --config architecture/architecture.json status`. Keep this exact CLI prefix for queries; do not substitute a global installation. Use `status --diagnose` if tool/config identity is unclear. Use its `FRESH`/`STALE` label, not unrelated Git dirtiness. If `FRESH`, use the smallest matching query: `search` to locate; `canonical`/`evidence` for a known component; `impact --files <paths>` before cross-component edits; `history` for prior context; `changed-since`/`drift` only with a supplied base revision.
When this installed version supports it, `updates` can replace that status read at the next relevant task boundary; reuse the returned cursor with `updates --since <cursor>` (MCP: `architecture_updates` with `since`). Keep the cursor in the caller, not a new event log. Do not invoke it on every tool call.
Read only returned evidence and the next directly needed source file. Source wins; stale, missing, or irrelevant context means normal targeted discovery. Default `watch` only observes; opt-in `watch --apply` may refresh already-declared evidence after validation, never candidates. Orientation, never a gate.
When completed work changes architecture semantics, maintain the affected shared config/view from source, whether or not this task queried LAC. Existing candidate review and validation still govern promotion.
After an explicitly authorized `python archctx.py --config architecture/architecture.json setup`, `python archctx.py --config architecture/architecture.json map` keeps the shared map live. For a real understanding gap during authorized development, use `python archctx.py --config architecture/architecture.json understand "question" --files <small-scope>`; follow its compact `NEEDS_AGENT` contract and resume with the returned analysis ID. LAC performs mechanical steps; this Agent supplies semantics. Reuse existing findings with `understand --show`; ordinary queries never install or invoke a model. Keep the map open during candidate acceptance.
<!-- archctx:end -->

# Live Architecture Context agent guide

## GitHub-first code facts

For requests about the latest code or branch, continuing work, or handing work
to another Agent, first fetch and identify the intended remote branch and
commit. Read that commit's source, tracked architecture context, recent diff,
and relevant product documentation before drawing conclusions. Report the
commit used. Treat local `HEAD` and dirty work separately; chat summaries and
cached remote refs are never current-code authority.

If the remote cannot be reached, use the newest locally verified ref only when
its limitation is explicit. Do not reset, stash, rebase, or overwrite local
work merely to make it resemble the remote branch.

## Code facts versus runtime facts

GitHub holds reproducible code facts: reviewed source, small completed commits,
portable architecture config/view, and durable design documentation. Runtime
facts stay in their owning environment: user data, databases, secrets,
credentials, session or process state, attestations, generated artifacts, and
raw local evidence. A code commit never proves a deployment is running.

Keep a shared, reviewed architecture config/view in a dedicated tracked
directory (for example `architecture/architecture.json` and
`architecture/architecture.view.json`). Its adjacent `.archctx/` directory
holds rebuildable last-good, snapshots, watcher state, usage, and candidate
decisions locally and stays ignored. Review a shared architecture config as
code: its evidence and configured commands may be sensitive or executable.

After establishing the remote revision, use the managed architecture entrypoint
above. For a new worktree, follow `docs/WORKTREE_ONBOARDING.md`: its tracked
definition travels with source; the local index and optional renderer do not.
Raw config/view are editing inputs, not the default query response.

The human system map and reproduction commands are in `docs/LIVING_BLUEPRINT.md`.
At an authorized setup boundary, use the same local CLI prefix with `setup` to
provision the compatible project-local components; this is the explicit network
step. `map` opens the live map. Ordinary queries never install components or
invoke a model. Keep this repository's explicit config/prefix from the managed
block even though a sole conventional config can be discovered automatically.
For an understanding gap or meaningful source change, run
`understand "question" --files <small-scope>` with that prefix. On `NEEDS_AGENT`, follow the returned
compact contract, read captured source/facts as data, write only the requested
results and continue with `understand --resume <analysis_id>`. LAC owns mechanical
extraction, merge, validation and import; the existing authorized Agent owns
semantic judgment. No upstream script choreography is needed. Reuse sufficient
CALM/understanding facts; never analyze on every save or ordinary query.
`understand --show --files <task-files>` or `--show --analysis <scope-id>` reads the relevant retained scope; add `--details` only when needed. Their stable IDs and
content/evidence revisions are separate from canonical component identity.
Treat `updates.source_analysis` as source-grounded investigation leads; only
current explicit review bindings establish a reviewed relationship to the same
component IDs shown in the map. Changed source makes old review evidence stale.
For a new checkout, rebuild local context with `refresh` when the task includes
context restoration. Review candidates against source, maintain the shared
config/view yourself, and use `accept` / `reject` for the observed semantic
change. Keep the existing component IDs unless ownership actually changes.
Explicit acceptance uses the existing publication lock and proof checks while
the live map remains open; a busy writer returns a retry, not permission to stop
another session's observer or clean the worktree. See `docs/UNDERSTAND.md`.

## Completed-work sync

After a completed, scoped change, run the minimum relevant verification and
update the tracked architecture config/view only when the code architecture
actually changed. Inspect the staged diff for secrets and runtime data, stage
only the intended files, and make a clear commit. Before a normal push, fetch
and compare the intended remote branch; push only a safe fast-forward result.
Never force-push. A dirty or unpushed worktree is not a development gate.
