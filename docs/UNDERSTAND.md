# Optional Understand Anything source analysis

LAC invokes the real, pinned Understand Anything (UA) toolchain and uses its
agent instructions through the coding agent's existing authorized execution.
It does not create another model service or replace the accepted architecture.
No provider runs on a query, viewer poll or ordinary save.

## One-time optional provider setup

Use a separate development directory, not another consumer's installation:

```sh
git clone https://github.com/Egonex-AI/Understand-Anything.git <provider>
git -C <provider> checkout --detach 5feed1f2ce4f9c368d860f4c0ebc36d98a4693fc
# In <provider>, using Node.js and the pinned pnpm 10.6.2:
pnpm --filter '@understand-anything/skill...' install --frozen-lockfile --ignore-scripts
pnpm --filter '@understand-anything/core' build
```

On Windows use `pnpm.cmd` where PowerShell execution policy requires it.
The filtered closure builds core and uses its packaged WASM grammars; it does
not install/run the UA dashboard. LAC checks the exact clean tracked provider
revision and compiled core. It never installs dependencies implicitly. Keep
the upstream MIT license and copyright with that checkout (see `NOTICE.md`).

## Analyze saved source, not a fabricated clean commit

From the LAC checkout (or use installed `archctx-understand`), select only files
needed for a real question. The config selects the actual repository and local
state; do not feed its component declarations to UA as substitute source.

```sh
python archctx_understand.py --config architecture/architecture.json prepare \
  --provider <provider> --files archctx.py archctx_development.py archctx_blueprint.py
```

PowerShell can use the same command on one line. The result identifies `input`,
`source_root` and `incremental.analyze_files`. It captures exact saved bytes,
product HEAD as provenance, worktree identity and provider revision; initializes
only the isolated source directory as a Git root, with **no snapshot commit**;
then runs upstream `scan-project.mjs` and `extract-import-map.mjs`. Dirty product
files remain untouched. High-level UA worktree redirection and signature-only
COSMETIC skipping are deliberately not used.

The existing authorized Codex session now performs upstream's actual semantic
stages. Read the pinned `understand-anything-plugin/skills/understand/SKILL.md`,
`agents/file-analyzer.md`, `agents/architecture-analyzer.md`,
`agents/assemble-reviewer.md`, `agents/tour-builder.md` and the referenced graph
guide. Use the supplied isolated source, not the primary checkout:

1. For each `incremental.analyze_files` entry, use its numbered
   `.ua/tmp/ua-file-analyzer-input-N.json`; run upstream's real
   `extract-structure.mjs` to `.ua/tmp/ua-file-extract-results-N.json`, then write
   the agent's semantic `batch-N*.json`. Preserve surviving `previousSymbols`
   IDs, raw relation types/directions and uncertainty. Do not copy old semantics
   for changed source. Unchanged files already have a retained baseline and
   extraction; do not re-dispatch them.
2. Run `python <provider>/understand-anything-plugin/skills/understand/merge-batch-graphs.py <source_root>`.
   Inspect counts and warnings. Complete the upstream architecture and tour
   stages against the actual assembled graph, producing `layers.json` and
   `tour.json`. Layers group understanding, not canonical ownership. Never
   fabricate upstream incremental Git fields for the unborn isolated snapshot.
3. Finish the upstream graph and import it:

```sh
python archctx_understand.py --config architecture/architecture.json finish --input <input>
python archctx_understand.py --config architecture/architecture.json import --input <input>
python archctx.py --config architecture/architecture.json candidates
```

`finish` creates actual `.ua/knowledge-graph.json`, validates it with UA's real
schema and builds fingerprints. Import requires exact scope coverage, successful
structural extraction and matching saved source. It does not use UA's lossy
sanitizer as a correctness oracle. Incomplete/stale results retain previous
discoveries and LKG; absent symbols/files are not deletion authorization.

## Reconcile, accept and see the same architecture

Existing `candidates` and `updates` return independent `source_analysis`.
`python archctx_understand.py ... show` returns bounded investigation details.
Default responses retain two raw edge witnesses per group; use `show --details`
for all retained witnesses and explicit omission counts. This keeps group
discovery useful inside the shared 16 KiB update budget.
An evidence overlap identifies where to look, not who canonically owns it.
Read actual source, preserve existing component IDs, and edit affected shared
config/view yourself. Leave uncertainty pending or use the existing rejection
reasons. The user need not maintain JSON manually.

```sh
python archctx.py --config architecture/architecture.json accept ua:<id> --bind component:<existing-or-reviewed-new-id>
python archctx.py --config architecture/architecture.json canonical <id>
python archctx_blueprint.py --live
```

Acceptance reuses the writer lock and validated Archify/LKG publication. An
explicit UA acceptance checks the same raw graph and saved-source hashes before
and after rendering. An unchanged canonical match records only historical review
provenance. That history does not prove current source/runtime freshness.
The page separates the accepted diagram from analysis findings and captured
analysis-source links; its existing source navigation and Before/After remain.
For a proof-bound explicit acceptance, avoid racing a live observer's automatic
definition promotion: use its non-`--live` mode while editing/accepting, then
resume your own live viewer. Do not stop another session's observer.

Saved source changes make analysis stale through the existing metadata observer.
At the next relevant task boundary, repeat `prepare` on the useful explicit scope.
Exact unchanged bytes **and** unchanged resolved imports reuse old file results;
changed files receive previous symbol identities. Incoming relations from retained
callers must survive merge, or the update fails for targeted source review.
Partial scope never automatically removes old canonical components. Domain/flow
analysis and automatic host scheduling are not wired; Codex owns this boundary.

## Cost and retention boundaries

The scope is at most 64 files / 4 MiB saved source; raw imported graph 8 MiB;
current analysis receipt 64 KiB; existing `updates` remains 16 KiB with omissions.
Idle observers only stat the receipt and at most 64 selected paths. Queries read
bounded hashes/summary, not raw graphs and not models. Eight local input runs are
retained before optional preparation asks the owner to archive obsolete runs;
there is no automatic history deletion. Upstream dependencies and extraction
files still have real disk/initial-analysis costs, not a token-saving promise.

All snapshots, raw graphs, fingerprints and semantic notes stay in ignored
adjacent `.archctx/understand/`; only reproducible source/config/view/docs enter
GitHub. Provider coverage can miss lazy imports, embedded languages and some
cross-batch calls. An absent raw edge is never proof that no dependency exists.
