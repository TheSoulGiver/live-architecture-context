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

For architecture orientation, after establishing the remote revision, run
`python archctx.py --config architecture/architecture.json status` before
reading raw architecture JSON or whole implementation files. If local context
is missing and the task requests restoration, use the setup/refresh commands
below, then start with `search <task terms>`, `canonical <returned-id>`,
`impact --files <paths>`, or `history --limit 3` on that same CLI prefix.
Read the returned source anchors and only the next necessary wiring. Raw
config/view are editing inputs, not the default query response. If context is
stale or irrelevant, do targeted source discovery; never guess or block work.

The working human map and reproduction commands are in `docs/LIVING_BLUEPRINT.md`.
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
