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

Keep a shared, reviewed architecture config/view in a tracked repo-relative
path (for example `architecture.json` and `architecture.view.json`). Keep
rebuildable `.archctx/` state—last-good, snapshots, watcher state, usage, and
candidate decisions—local and ignored. Review a shared architecture config as
code: its evidence and configured commands may be sensitive or executable.

## Completed-work sync

After a completed, scoped change, run the minimum relevant verification and
update the tracked architecture config/view only when the code architecture
actually changed. Inspect the staged diff for secrets and runtime data, stage
only the intended files, and make a clear commit. Before a normal push, fetch
and compare the intended remote branch; push only a safe fast-forward result.
Never force-push. A dirty or unpushed worktree is not a development gate.
