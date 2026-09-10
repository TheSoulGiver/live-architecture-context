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

LAC captures exact bytes, worktree identity and source revision, then advances
mechanical work to the next semantic boundary. On `NEEDS_AGENT`, the response
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

Completed work is reused by content identity. Unchanged saved bytes and resolved
imports retain file results; changed files receive previous symbol identities.
Retained incoming relations must survive the merge. A complete matching result
returns `REUSED` without new semantic work. A source change during a run returns
`STALE`; repeat understanding for the relevant current scope.

## Review into the same system map

```sh
archctx understand --show --details
archctx --config architecture/architecture.json candidates
archctx --config architecture/architecture.json accept <returned-finding-id> --bind component:<existing-or-reviewed-new-id>
archctx --config architecture/architecture.json canonical <component-id>
archctx map
```

`understand --show --details`, `candidates` and `updates` read existing
findings without provider execution. Findings carry a stable `id`, a semantic
`content_revision` and a source-bound `evidence_revision`. Source overlap
identifies where to investigate, not canonical ownership. The Agent reviews the
actual source, updates affected shared config/view declarations and uses existing
`accept` / `reject` decisions. Preserve component IDs unless ownership changes.

A current explicit decision supplies component/relation `bindings`.
`related_components` contains the same component IDs used by `canonical` and
the map; a reviewed relation contributes its declared endpoint IDs. Semantic or
relevant source changes invalidate the prior review binding. Historical review
does not prove current source freshness. Partial analysis cannot authorize
canonical component or relation deletion.

Acceptance shares the existing writer lock, validated refresh and last-good
publication. It rechecks the captured graph/source proof and the actual
config/view inputs, including when the live observer has already published the
same definition. A busy writer returns a retry. There is no need to stop the map
or another session's observer. Uncertain findings remain non-blocking leads.

The [system map](LIVING_BLUEPRINT.md) separates confirmed components, discovered
findings and changed source. Selecting a component shows its related findings,
accepted evidence and exact Agent queries. Source-understanding freshness and
accepted-architecture freshness remain distinct. Captured source links describe
historical analysis bytes, not the current worktree.

## Boundaries and compatibility

The scope remains at most 64 files / 4 MiB saved source; imported raw graph 8 MiB;
current receipt 64 KiB; existing `updates` 16 KiB with explicit omissions.
Idle observers check bounded metadata. Eight local input runs are retained
before preparation asks the owner to archive obsolete runs; no automatic
history deletion is introduced. Raw graphs, snapshots, semantic results and
tool installations stay in ignored adjacent `.archctx/` state.

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
