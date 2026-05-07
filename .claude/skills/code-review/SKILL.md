---
name: code-review
description: Review DuckDB and ADL-OPT changes in this checkout with project-specific checklists. Use when Codex is asked to review a diff, PR, branch, recent implementation, optimizer or join-order change, ADL-OPT harness artifact change, documentation change, test change, or generated experiment output for correctness, research boundaries, reliability, security, and coverage.
---

# Code Review

## What To Do

Review code in a findings-first style. Prioritize bugs, behavioral regressions, missing tests, correctness risks, and stale assumptions. Keep summaries brief and secondary.

For non-trivial diffs, first read `AGENTS.md`, `ARCHITECTURE.md`, and the relevant design docs linked there. Use the actual code as the source of truth when docs disagree, and mention the mismatch.

## Review Workflow

1. Identify the review target with `git status --short`, `git diff --stat`, and the relevant diff or PR range.
2. Read the changed files and their nearest callers/callees. For optimizer changes, trace the query path from parser/planner through optimizer, join-order enumeration, physical planning, execution, and `EXPLAIN`/profiling as needed.
3. State the intended goal of the change, then check whether the implementation and tests prove that goal.
4. Lead the response with concrete findings ordered by severity. Use file and line references. If there are no findings, say so and note residual test gaps or risk.
5. Do not require item-by-item checklist output unless the user asks for it, but use the checkpoints below while reviewing.

## Core Invariants

Always check these first:

- DuckDB SQL semantics must not change accidentally. Query results, errors, null behavior, join semantics, and optimizer-visible behavior must remain compatible unless the task explicitly intends a behavior change.
- ADL-OPT v0 is an offline research harness plus narrow debug export path. It must not modify DuckDB public APIs or normal optimizer behavior.
- R5 IKKBZ linearization is export-only. It may write debug metadata or add `EXPLAIN` summary under debug settings, but it must not apply exported orders to DuckDB plans.
- Experiments must fail closed. Never claim a speedup unless SQL result correctness and plan-control evidence are both present.
- Generated JSONL/JSON artifacts must be reproducible enough to audit: query id, scale factor, variant/baseline, seed, SQL hash, `EXPLAIN` hash, row count/checksum, timing, timeout, and failure status where relevant.
- Debug settings and output paths must be local, opt-in, and safe when disabled. Default DuckDB behavior should not emit ADL-OPT output.
- Unsupported joins, disconnected graphs, malformed SQL variants, missing profiling, and timeouts are structured failures, not silent skips.
- Do not commit large generated databases, model checkpoints, private paths, credentials, or third-party dataset artifacts unless intentionally curated and licensed.

## DuckDB Optimizer Review

Start at these files when join-order behavior is touched:

- `src/optimizer/optimizer.cpp`
- `src/optimizer/join_order/join_order_optimizer.cpp`
- `src/optimizer/join_order/query_graph_manager.cpp`
- `src/optimizer/join_order/plan_enumerator.cpp`
- `src/optimizer/join_order/cardinality_estimator.cpp`
- `src/optimizer/join_order/cost_model.cpp`
- `src/optimizer/join_order/adl_opt_join_linearizer.cpp`
- `src/execution/physical_plan/plan_explain.cpp`
- `src/main/client_context.cpp`
- `src/include/duckdb/main/settings.hpp`

Check:

- Reorderability boundaries: outer, ASOF, MARK, SINGLE, dependent/delim, non-inner, or non-regular join graphs must not be treated as regular inner pair joins.
- Relation ids and labels: internal debug labels like `r0` are not SQL aliases. JSON consumers must not confuse them.
- Cardinality and cost estimates: avoid division-by-zero, negative/NaN selectivity, overflow, unstable tie-breaking, and accidental mutation of enumerator state.
- Exact vs approximate path: DuckDB's `n <= 12` exact DPhyp path is not the ADL-OPT target. Large-join behavior around the threshold must be deliberate.
- Plan reconstruction: export/debug code must not mutate `plans`, selected join tree, filters, logical operators, or physical plan generation unless explicitly scoped and tested.
- `EXPLAIN` integration: ADL-OPT summary appears only when enabled and should be cleared per query/session path so stale metadata cannot leak into unrelated explains.
- Settings: new settings need conservative defaults, local scope if intended, stable names, comments/descriptions, and no accidental public API promise.

## ADL-OPT Harness Review

Read the current spec and schema when harness output changes:

- `docs/product-specs/adl-opt-research-spec.md`
- `docs/design-docs/feature-and-label-schema.md`
- `docs/design-docs/experiment-scale-and-benchmark-protocol.md`
- `docs/design-docs/duckdb-join-order-integration.md`
- `docs/design-docs/ikkbz-linearization-export-usage.md`
- `scripts/adl_opt/offline_tpch_harness.py`
- `scripts/adl_opt/offline_large_join_harness.py`
- `scripts/adl_opt/README.md`

Check:

- Connected-state enumeration only emits valid connected subsets and adjacent append transitions.
- v0 hints append one adjacent relation to the current connected subset; large-join endpoint mode appends an endpoint over an existing linear order.
- SQL variants preserve results against DuckDB default using row count and checksum.
- Fixed-order variants record `EXPLAIN` evidence and mark uncontrolled plans instead of comparing them as valid speedups.
- Random baselines record seeds and selected transition paths.
- TPC-H smoke uses SF 0.1 when feasible and SF 0.01 as a recorded fallback.
- JOB/IMDB large-join artifacts stay static unless the change explicitly adds executable support and documents dataset licensing/setup.
- JSONL records follow the schema, remain line-delimited and parseable, and keep nulls or structured failure objects for missing data.
- Summary metrics do not hide failures, invalid plans, timeouts, or excluded variants.

## Documentation Review

For ADL-OPT docs, check:

- Chinese body, English TL;DR, `Updated:` line, and key terms.
- Durable knowledge belongs in linked docs, not bloated into `AGENTS.md`.
- `CLAUDE.md` remains preserved as a source reference.
- ADL-OPT security notes belong in `docs/SECURITY.md`; do not overwrite root `SECURITY.md`.
- Commands, paths, and acceptance criteria match the current scripts and C++ settings.
- Claims about R5 must say export-only and must not imply DuckDB uses IKKBZ orders.

## Testing Review

Prefer focused evidence:

- C++ changes: `make reldebug` when practical, `build/reldebug/test/unittest`, or a targeted sqllogictest.
- SQL behavior: prefer sqllogictest `.test` files when C++ tests are not needed.
- ADL-OPT scripts: `python3 -m py_compile scripts/adl_opt/offline_tpch_harness.py scripts/adl_opt/offline_large_join_harness.py`.
- Static harness smoke: `python3 scripts/adl_opt/offline_tpch_harness.py --output /tmp/adl-opt-run --queries q03 q05 q08`.
- Large-join static smoke: `python3 scripts/adl_opt/offline_large_join_harness.py --output /tmp/adl-opt-large-static`.
- R5 smoke: `./build/reldebug/duckdb /tmp/adl-opt-r5-smoke.duckdb < scripts/adl_opt/r5_ikkbz_linearization_smoke.sql`.
- Formatting: `make format-fix` or at least `git diff --check`.

When tests are missing, name the smallest test that would expose the risk. Do not accept performance claims from a single uncontrolled or incorrect run.

## High-Risk Patterns

Flag these aggressively:

- ADL-OPT code changing DuckDB's chosen plan outside an explicitly reviewed behavior-change task.
- Debug/export code running when settings are disabled.
- Stale `EXPLAIN` metadata from a previous query.
- Treating unsupported join graphs as valid by dropping filters or edges.
- Comparing latencies when result checksum, row count, or plan-control evidence is absent.
- Randomized artifacts without a recorded seed.
- Handwritten generated outputs that should come from the harness.
- Absolute local paths, private dataset paths, credentials, or bulky generated files in committed artifacts.
- Broad optimizer rule switches used as the primary ADL-OPT hint representation.
- Docs claiming implemented model inference, endpoint decisions, or true linearization when the code only emits fixtures or metadata.
