# Source understanding in the development loop

LAC captures a small saved-source scope, runs its compatible extraction tools
and asks the current authorized Agent for semantic judgment. The same product
imports the result, relates it to existing component IDs and displays it in the
system map. No additional model service, dashboard or upstream manual is part
of the normal workflow. Ordinary queries, map polls and saves invoke no model.

## Set up this project once

With Python 3.10+, Git and Node.js 20+, run from the project root:

```sh
archctx setup
```

This is the explicit network operation. LAC provisions fixed compatible
source-understanding and rendering components in ignored project-local state,
builds the required analysis package, and preserves reviewed config/view
definitions. A new project starts with empty component declarations; setup
does not invent ownership. An incompatible or modified existing installation
is preserved and reported. Diagnostic overrides can select already-prepared
external components; LAC does not install into those locations.

The native `setup`, `understand` and `map` commands automatically select the sole
conventional `architecture/architecture.json` or `.archctx/architecture.json`.
When both exist, or a different location is needed, use an explicit `--config`.
Existing query/acceptance commands still require an explicit shared config;
keep the same prefix consistently when moving between these operations.
In this source checkout, keep the managed prefix
`python archctx.py --config architecture/architecture.json` for every command.

## Ask about saved source

```sh
archctx understand "How does this request reach storage?" --files src/service.py src/storage.py
```

Select the useful files for the current question, including saved uncommitted
changes. Without `--files`, LAC can use a bounded canonical search to select
known evidence files; insufficient orientation returns `NEEDS_SCOPE` for the
Agent to choose a scope. It does not silently claim whole-repository coverage.

LAC captures exact selected bytes, worktree identity and source revision in an
isolated source directory. Its pinned parser/resolver also supplies static
dependency paths; LAC captures bounded dependency bytes, resolver configuration
and the repository's file-name inventory. Missing, unsupported, dynamic or
uncaptured dependencies remain `UNKNOWN`, not proof of no dependency. Changed
dependency bytes or import-resolution inventory invalidate reuse and current
review bindings, even when the selected file is unchanged.

Mechanical work then advances to the next semantic boundary. On `NEEDS_AGENT`,
the response
contains the `lac.source-understanding/v1` contract and exact input/output
paths. The Agent reads source and extraction as data, preserves surviving
symbol IDs and raw edge meaning, and writes only the requested semantic results.
Each file result binds the supplied `input_hash`; a system result binds the
supplied graph hash. Understanding groups and the ordered tour are investigation
aids, not canonical components or proven runtime flows.

```sh
archctx understand --resume <analysis_id>
```

Resume performs remaining extraction, merges completed file results and, when
needed, returns the next `NEEDS_AGENT` request for groups and a tour. Repeat
after writing those results. LAC then validates, finishes and imports the graph,
returning `FINDINGS_READY`. It checks source and result identities before
publication; incomplete or changed inputs retain the previous usable analysis
and accepted architecture. The map can remain open throughout.

The project retains independently scoped results. Understanding B does not
replace A; a query can return to A without invoking another model. Each scope
has its own source freshness and current analysis identity. Imports update the
project catalog under the existing writer lock; independently prepared scopes
can both publish, while a late result cannot overwrite a newer result for the
same scope. Retained graphs are not combined into canonical architecture.
The preparation's predecessor remains fixed across `NEEDS_AGENT` and resume,
including recovery of mechanical inputs. A superseded result requires reading
and re-reviewing the current findings; resume never silently rebases it. An
identical already-published result remains an idempotent read.

Completed work is reused by content and captured dependency identity. Unchanged
saved bytes and resolved imports retain file results; changed files receive
previous symbol identities.
Retained incoming relations must survive the merge. A complete matching result
returns `REUSED` without new semantic work. A source change during a run returns
`STALE`; repeat understanding for the relevant current scope.
Automatic system reuse reads the complete hash-verified retained graph, not the
eight-item `previous_system` preview. Layers and ordered tour steps remain whole
in storage. If that full evidence is missing or changed, `NEEDS_AGENT` requests
complete groups and tour; the preview cannot stand in for omitted content.

## Review into the same system map

```sh
archctx understand --show --details
archctx understand --show --analysis <analysis-or-scope-id> --details
archctx understand --show --files src/service.py
archctx --config architecture/architecture.json candidates
archctx --config architecture/architecture.json accept <returned-finding-id> --analysis <analysis-or-scope-id> --bind component:<existing-or-reviewed-new-id>
archctx --config architecture/architecture.json canonical <component-id>
archctx map
```

`understand --show` reads existing findings without provider execution.
`--analysis` selects one retained analysis or stable scope ID; `--files` focuses
the result on requested paths. The default follows the current scope. Responses
include independently labelled scope summaries and explicit uncovered paths;
one scope's `FRESH` status is not a project-wide freshness claim. `updates` also
accepts `--analysis` or `--files`. These queries and the map's scope selector
only read findings and do not start analysis. `accept` and `reject` can use
`--analysis` to select the finding's retained scope.

Findings carry a stable `id`, a semantic
`content_revision` and a source-bound `evidence_revision`. Source overlap
identifies where to investigate, not canonical ownership. The Agent reviews the
actual source, updates affected shared config/view declarations and uses existing
`accept` / `reject` decisions. Preserve component IDs unless ownership changes.

A current explicit decision supplies component/relation `bindings`.
`related_components` contains the same component IDs used by `canonical` and
the map; a reviewed relation contributes its declared endpoint IDs. Semantic or
relevant source or captured dependency changes invalidate the prior review binding.
Unknown dependency coverage cannot inherit an earlier review. Historical review
does not prove current source freshness. Partial analysis cannot authorize
canonical component or relation deletion.

Acceptance shares the existing writer lock, validated refresh and last-good
publication. It rechecks the captured graph/source proof and the actual
config/view inputs, including when the live observer has already published the
same definition. A busy writer returns a retry. There is no need to stop the map
or another session's observer. Uncertain findings remain non-blocking leads.

The [system map](LIVING_BLUEPRINT.md) separates confirmed components, discovered
findings and changed source. Its scope selector shows current, changed and
unknown analysis scopes without replacing the accepted diagram. Selecting a
component shows its related findings,
accepted evidence and exact Agent queries. Source-understanding freshness and
accepted-architecture freshness remain distinct. Captured source links describe
historical analysis bytes, not the current worktree.

## Boundaries and compatibility

Each request selects at most 64 files. Selected source, captured dependency
bytes and resolver configuration share a 4 MiB budget; raw graphs remain bounded
to 8 MiB, each receipt to 64 KiB and the project catalog to 256 KiB. Compact
queries show at most eight scope summaries with explicit omissions; this is
a response bound, not an eight-run retention limit. `updates` retains its
16 KiB response budget. Idle observers stat bounded known paths and receipts.

Analysis storage uses a 256 MiB budget based on retained file sizes, checked at
explicit write boundaries. Under pressure it can reclaim only known mechanical
cache files from completed, unreferenced runs. Catalog/review references,
active or resumable runs, legacy runs and keep-marked data remain protected;
original source, imported graphs, receipts and Agent semantic results are
retained. If reclaimable space is insufficient, that capacity check removes
nothing and reports used, reserved and protected bytes. Raw evidence and tool
installations stay in ignored adjacent `.archctx/` state.

Source extraction uses the pinned Understand Anything revision
`5feed1f2ce4f9c368d860f4c0ebc36d98a4693fc`; rendering uses Archify
`5de7275fe87a66a19d52a4d9b0b3a4f2a5a90115`.
Provider identity is diagnostic provenance, not a separate everyday entrypoint.
Keep upstream licenses with local installations (see [NOTICE.md](../NOTICE.md)).
Lazy imports, embedded languages and cross-batch calls can be missed; absent
edges are not proof of no dependency. Dedicated domain/flow analysis, complete
runtime reachability and automatic host scheduling are not supplied. CALM remains
a separate optional code-fact provider.

The expert `archctx-understand prepare/finish/import/show`,
`python archctx_understand.py`, `archctx-blueprint` and
`python tools/archify.py` entrypoints remain compatible. They are useful for
existing integrations or diagnosis; normal development uses
`archctx setup`, `archctx understand` and `archctx map`.
