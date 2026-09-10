<!-- archctx:begin -->
## Architecture context

Use Archctx only when it shrinks the next broad source read (canonical/truth/evidence, cross-component path, freshness/delta, or legacy ambiguity); skip obvious local work.
From the repository root, run `python archctx.py --config architecture/architecture.json updates` when orientation or freshness needs checking; reuse its cursor with `updates --since <cursor>` at the next relevant read. Keep this exact CLI prefix for queries; do not substitute a global installation. Use `status --diagnose` if tool/config identity is unclear. Use its `FRESH`/`STALE` label, not unrelated Git dirtiness. If `FRESH`, use the smallest matching query: `search` to locate; `canonical`/`evidence` for a known component; `impact --files <paths>` before cross-component edits; `history` for prior context; `changed-since`/`drift` only with a supplied base revision.
Read only returned evidence and the next directly needed source file. Source wins; stale, missing, or irrelevant context means normal targeted discovery. Default `watch` only observes; opt-in `watch --apply` may refresh already-declared evidence after validation, never candidates. Orientation, never a gate.
For a modification, read `impact.change_scope`: direct components, declared dependencies and dependents, with accepted and unaccepted working definitions kept separate. Raw relation direction is not necessarily a dependency; legacy reach fields are compatibility only. Use the same files with `--details` when witnesses are omitted, and compare context/config hashes with the page before comparing answers.
Whether or not this task queried LAC, maintain affected shared definitions/view when responsibilities, canonical entries, relations, or trust boundaries actually change; leave uncertain signals as candidates. Ordinary implementation changes do not need new architecture declarations.
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

The working human map and reproduction commands are in `docs/LIVING_BLUEPRINT.md`.
When `updates.source_analysis` contains relevant findings, treat them as
provider-labelled investigation leads, not accepted ownership. For an initial
understanding gap or meaningful source change, use `docs/UNDERSTAND.md` with an
explicit small scope and the existing authorized Codex session. Reuse available
CALM/analysis facts when sufficient; never analyze on every save or query.
For a new checkout, rebuild local context with `refresh` when the task includes
context restoration. Review candidates against source, maintain the shared
config/view yourself, and use `accept` / `reject` for the observed semantic
change. Keep the existing component IDs unless ownership actually changes.

## Completed-work sync

After a completed, scoped change, run the minimum relevant verification and
update the tracked architecture config/view only when the code architecture
actually changed. Inspect the staged diff for secrets and runtime data, stage
only the intended files, and make a clear commit. Before a normal push, fetch
and compare the intended remote branch; push only a safe fast-forward result.
Never force-push. A dirty or unpushed worktree is not a development gate.
