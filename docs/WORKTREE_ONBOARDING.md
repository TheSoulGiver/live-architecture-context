# Restore architecture context in a fresh worktree

Keep reviewed definitions with source; rebuild state here. Do not copy another
worktree's last-good, install a global upgrade, or guess a missing definition.
Run commands from the intended repository root. Availability is not evidence
of adoption or net token savings.

## This repository (no package installation needed)

```sh
python archctx.py --config architecture/architecture.json status --diagnose
python tools/archify.py setup
python archctx.py --config architecture/architecture.json init --command "python archctx.py"
python archctx.py --config architecture/architecture.json canonical refresh-transaction
python archctx_blueprint.py --config architecture/architecture.json
```

`status --diagnose` identifies the running core source, interpreter, selected
config and state paths without writes or configured command execution. This
source checkout is the tool: a stale `archctx` on PATH cannot replace it.
`setup` explicitly fetches the pinned Archify renderer. `init` reuses the
tracked definition, installs the existing managed AGENTS block, and refreshes
through its reviewed validators. `--check` previews onboarding without writes
or executing those validators. Neither setup nor refresh starts a watcher;
use the existing [live loop](LIVING_BLUEPRINT.md) when needed.

The query and viewer select the same `architecture/architecture.json` and its
adjacent `architecture/.archctx/` state. A new worktree starts MISSING, even if
another worktree is FRESH. Valid refresh promotes its own accepted context and
Archify generation. Failed evidence or rendering retains local LKG as stale.

## A consumer project

1. Fetch the intended code revision. Review existing architecture material
   against current source. Keep the chosen LAC config and optional view in a
   tracked directory, for example `architecture/architecture.json` with
   `"repo":".."`. Preserve other configurations; always select this one
   explicitly. Review `gates`, `code_graph` and `archify` argv as executable
   code. A source observation or old diagram does not authorize those programs.
2. Create a local Python environment inside ignored `.archctx/venv`. Install
   a reviewed, immutable LAC commit into it; never reuse another worktree's
   environment or silently replace the shared CLI.
3. Give the owner the explicit command below (or bind it to an existing
   project script). Use the same prefix in AGENTS, queries, refresh and watch.

Windows PowerShell, from the consumer root:

```powershell
python -m venv .archctx/venv
.archctx/venv/Scripts/python.exe -m pip install "git+https://github.com/TheSoulGiver/live-architecture-context.git@<reviewed-commit>"
.archctx/venv/Scripts/python.exe -I -m archctx --config architecture/architecture.json status --diagnose
.archctx/venv/Scripts/python.exe -I -m archctx --config architecture/architecture.json init --command ".archctx/venv/Scripts/python.exe -I -m archctx"
.archctx/venv/Scripts/python.exe -I -m archctx --config architecture/architecture.json search "<task capability>"
.archctx/venv/Scripts/python.exe -I -m archctx_blueprint --config architecture/architecture.json
```

On Linux/macOS use `.archctx/venv/bin/python` in place of
`.archctx/venv/Scripts/python.exe`, including the `--command` prefix. `-I`
prevents a worktree module or inherited `PYTHONPATH` from shadowing the installed
core. No environment activation or global PATH selection is required. For a
cross-platform team, put the platform-specific executable choice in an existing
project script and supply that script as `--command`; keep its config explicit.
The installer records guidance, not executable provenance: verify actual
module/interpreter paths and core hash with diagnostics after installation.

The consumer owner must provide/review a real Archify view and local pinned
renderer commands to obtain a human blueprint. Reuse the project's existing
renderer; do not invent a code graph or copy this repository's component names.
The consumer's config/adapter belongs only in that consumer repository.

For a new declaration, the same explicit `--config ... init --component ...
--evidence 'relative/path::exact source anchor'` writes the requested path and
relative repo binding. Existing definitions are never replaced. Missing anchors
need a source review, not automatic weakening. If shared and private conventional
configs coexist, even `status` requires an explicit selection. Arbitrary custom
locations are never discovered. Configs representing independent contexts should
use separate tracked directories (or explicit, repository-local state dirs).
`init` ignores a selected custom state directory with an anchored literal rule
and refuses the repository root as state. Don't select a source directory as
state. Managed path quoting targets PowerShell on Windows and POSIX shells
elsewhere; the supplied command prefix remains reviewed instruction text.

Only source, reviewed config/view, and the small project entrypoint/instructions
travel through Git. `.archctx/` ignores at every depth cover the local
environment, retained context, receipts and generated blueprint. Confirm ignore
rules before committing; existing tracked runtime files are not untracked by init.
No startup hook, new service, adoption log, or development gate is required.

## What remains a human/Agent decision

The owner chooses a reviewed LAC revision, the intended config and relevant
source anchors; reviews external commands; and maintains architecture semantics
when source changes. A busy owner may defer adoption. Use ordinary task evidence
to distinguish availability, use, and benefit; don't poll for a trial or require
a token-savings experiment before continuing product development.
