# AGENTS.md

English TL;DR: Start here. This file is the short map for agents working in this DuckDB checkout and the ADL-OPT research harness.

Updated: 2026-05-06

Key terms: DuckDB, ADL-OPT, query optimizer, join order, harness, TPC-H

## Role of This File

This file is intentionally short. Keep durable project knowledge in the linked documents below, not here.

- DuckDB source architecture: `ARCHITECTURE.md`
- ADL-OPT research spec: `docs/product-specs/adl-opt-research-spec.md`
- ADL-OPT design notes: `docs/design-docs/`
- Active execution plans: `docs/exec-plans/active/`
- Completed execution plans: `docs/exec-plans/completed/`
- Generated maps and experiment artifacts: `docs/generated/`
- PR and branch workflow: `docs/design-docs/pr-flow.md`
- Offline harness scripts: `scripts/adl_opt/offline_tpch_harness.py`, `scripts/adl_opt/offline_large_join_harness.py`
- Paper summaries and terminology: `docs/references/`
- Quality, reliability, security: `docs/QUALITY_SCORE.md`, `docs/RELIABILITY.md`, `docs/SECURITY.md`

## Current ADL-OPT Default

The current phase is a research harness, not a DuckDB behavior change.

- Do not modify DuckDB public APIs or C++ optimizer behavior for ADL-OPT v0.
- Use TPC-H small scale for execution smoke tests, preferably SF 0.1 and fallback SF 0.01.
- Use JOB/IMDB 29/28/33 static artifacts for the n>12 large-join direction.
- Focus first on offline plan/data collection, connected join-state enumeration, profiling, and JSONL artifacts.
- v0 hint means: append one adjacent relation to the current connected join subset. Large-join follow-up narrows this to endpoint append over an existing linear order.
- Do not use global optimizer rule switches as the primary hint representation for ADL-OPT v0.

## Common Commands

Preferred development build:

```bash
make reldebug
```

Fast unit tests:

```bash
build/reldebug/test/unittest
```

Specific SQL test:

```bash
build/reldebug/test/unittest test/sql/order/test_limit.test
```

Formatting:

```bash
make format-fix
```

Build with TPC-H support when needed:

```bash
BUILD_TPCH=1 make reldebug
```

Generate static ADL-OPT v0 TPC-H JSONL artifacts:

```bash
python3 scripts/adl_opt/offline_tpch_harness.py --output /tmp/adl-opt-run --queries q03 q05 q08
```

Generate static ADL-OPT n>12 JOB/IMDB artifacts:

```bash
python3 scripts/adl_opt/offline_large_join_harness.py --output /tmp/adl-opt-large-static
```

## DuckDB Navigation

- `src/parser/`: SQL parsing.
- `src/planner/`: binding and logical plans.
- `src/optimizer/`: logical optimization, including join ordering.
- `src/optimizer/join_order/`: query graph, cost model, cardinality estimator, plan enumerator.
- `src/execution/physical_plan/`: physical plan generation.
- `src/main/`: database, connection, client context, C API, settings, profiling.
- `extension/tpch/`: TPC-H schema, data generation, query text.
- `benchmark/tpch/`: TPC-H benchmark harnesses.
- `test/sql/tpch/`: TPC-H SQL tests.

Start optimizer tracing at:

- `src/optimizer/optimizer.cpp`
- `src/optimizer/join_order/join_order_optimizer.cpp`
- `src/optimizer/join_order/plan_enumerator.cpp`
- `src/optimizer/join_order/query_graph_manager.cpp`

## Editing Rules for Agents

- Preserve `CLAUDE.md`; it is a source reference, not the active Codex map.
- Use feature branches and PRs into `origin/main` for non-trivial ADL-OPT changes.
- Keep ADL-OPT docs bilingual-friendly: Chinese body, English TL;DR, `Updated:` line, and key terms.
- Do not overwrite root `SECURITY.md`; ADL-OPT-specific security notes belong in `docs/SECURITY.md`.
- Use `rg` for search.
- Prefer sqllogictest `.test` files for DuckDB behavior tests when C++ tests are not required.
