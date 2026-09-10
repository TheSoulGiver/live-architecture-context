# Restore architecture context in a fresh worktree

Keep reviewed definitions with source; rebuild state here. Do not copy another
worktree's last-good, install a global upgrade, or guess a missing definition.
Run commands from the intended repository root. Availability is not evidence
of adoption or net token savings.

## This repository (no package installation needed)

```sh
python archctx.py --config architecture/architecture.json status --diagnose
python archctx.py --config architecture/architecture.json setup --command "python archctx.py"
python archctx.py --config architecture/architecture.json map
```

`status --diagnose` identifies the running core source, interpreter, selected
config and state paths without writes or configured command execution. This
source checkout is the tool: a stale `archctx` on PATH cannot replace it.
`setup` explicitly provisions compatible source-understanding and rendering
components in this worktree, preserves the tracked definition and installs the
managed AGENTS block. `READY` means tools are ready, not architecture acceptance.
`map` starts its own live observer and requests the existing validated refresh
when a local accepted baseline is missing. It keeps failed validation visible.
Leave the map running while developing. At a relevant understanding boundary,
follow the returned compact Agent contract and resume with
`understand --resume <analysis_id>`; see [source understanding](UNDERSTAND.md).
The previous `init --check` and expert entrypoints remain available for diagnosis.

While the map remains open, the Agent or another terminal can use the same prefix:

```sh
python archctx.py --config architecture/architecture.json understand "How is accepted context published?" --files archctx.py archctx_blueprint.py
python archctx.py --config architecture/architecture.json canonical refresh-transaction
```

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
.archctx/venv/Scripts/python.exe -I -m archctx --config architecture/architecture.json setup --command ".archctx/venv/Scripts/python.exe -I -m archctx"
.archctx/venv/Scripts/python.exe -I -m archctx --config architecture/architecture.json map
```

On Linux/macOS use `.archctx/venv/bin/python` in place of
`.archctx/venv/Scripts/python.exe`, including the `--command` prefix. `-I`
prevents a worktree module or inherited `PYTHONPATH` from shadowing the installed
core. No environment activation or global PATH selection is required. For a
cross-platform team, put the platform-specific executable choice in an existing
project script and supply that script as `--command`; keep its config explicit.
The installer records guidance, not executable provenance: verify actual
module/interpreter paths and core hash with diagnostics after installation.
Use this same explicit prefix for `search`, bounded `understand`, `--resume`
and acceptance from the Agent or another terminal while the map remains open.

The current native entrypoints are available in the reviewed commit containing
them; earlier releases retain their earlier onboarding contract. `setup` wires
the compatible renderer and a presentation view only when absent. Preserve and
review any existing custom commands/view. The Agent maintains source-grounded
component and relation declarations; neither empty setup nor generated positions
prove architecture. Do not invent a code graph or copy this repository's component
names. Consumer config and adapters remain only in their owning repository.

For a new declaration, use bounded `understand` with the existing Agent, follow
its contract/resume boundary, review the source and update shared config/view
before explicit `accept`. Keep the map open; acceptance shares its publication
lock and validates the same evidence. The expert `init --component ... --evidence
'relative/path::exact source anchor'` path remains compatible. Missing anchors
need source review, not automatic weakening. The three native entrypoints can
discover a sole conventional config; existing query commands retain the explicit
shared-config rule. If shared and private definitions coexist, choose explicitly.
Arbitrary custom locations are never discovered. Independent contexts should
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
This repository's optional `.codex/hooks.json` still needs exact host trust in
each new environment; without it, use the same `updates` query when relevant.
Codex CLI 0.153.4 [loads linked-worktree hooks from the primary checkout](https://github.com/openai/codex/blob/3d2ee51ca2d5db578f328aa75e20aa22c0197c9a/codex-rs/config/src/loader/mod.rs#L1127),
not from that worktree's `.codex/hooks.json`. Trust alone does not fix a missing
hook source, and an empty local `config.toml` does not change this rule. Use the
pull path until the actual primary-checkout hook source is reviewed/configured;
do not relocate a repository or alter another checkout just to enable notices.

## What remains a human/Agent decision

The owner chooses a reviewed LAC revision, the intended config and relevant
source anchors; reviews external commands; and maintains architecture semantics
when source changes. A busy owner may defer adoption. Use ordinary task evidence
to distinguish availability, use, and benefit; don't poll for a trial or require
a token-savings experiment before continuing product development.
