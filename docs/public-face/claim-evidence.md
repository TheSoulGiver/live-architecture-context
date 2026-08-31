# Public claim evidence

This file is the evidence ledger for README claims. It deliberately excludes
private repositories, source paths, prompts, configuration, and raw logs.

## Observed context efficiency

`benchmarks/observed-context-ab.json` records one paired fresh-session observation
from an isolated worktree in an anonymized private, long-lived repository:

| Metric | Direct repository exploration | With Live Architecture Context | Change |
| --- | ---: | ---: | ---: |
| Input context | 143,540 | 107,663 | -25% |
| Distinct file-token reads | 605 | 345 | -43% |

`tools/render_benchmark.py` derives the rounded changes and produces
`assets/context-efficiency.svg`. `--check` fails when the checked-in SVG and
the data disagree.

Scope: one observed real workflow. It is not a universal token, latency, cost,
or correctness benchmark. The public artifact reproduces the arithmetic and
visual from the aggregate values, not the private workflow.

## Product claims

| Claim | Executable/source evidence |
| --- | --- |
| Source remains authority; context is rebuildable | `archctx.py` validates configured source evidence before promotion and records revision/hash in `refresh`. |
| Stale context is not silently promoted | `archctx.py` returns `STALE` when evidence/revision/config differ; failed refresh reports `last_good_preserved`. |
| Compact Agent reads are intentional | `status` returns metadata only; `search` defaults to three results and reports omissions; `snapshot` is explicit. |
| Code graph facts are separate from authored architecture claims | `calm_query.py`, `trace`, and `impact` preserve separate provenance/confidence fields. |

The automated coverage in `test_archctx.py` exercises last-good preservation,
stale status, bounded search, watcher promotion, and MCP behavior on Windows
and Linux CI.
