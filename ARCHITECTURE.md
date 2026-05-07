# DuckDB + ADL-OPT Architecture Map

English TL;DR: DuckDB already has a clear optimizer pipeline. ADL-OPT mostly studies join-order decisions offline; R5 adds an export-only C++ debug path for n>=12 IKKBZ linearization metadata.

Updated: 2026-05-07

Key terms: parser, binder, logical plan, optimizer, join-order optimizer, physical plan, profiling, CCG

## DuckDB Query Path

DuckDB turns SQL into results through this high-level path:

```text
SQL
  -> parser
  -> binder / planner
  -> logical plan
  -> optimizer
  -> optimized logical plan
  -> physical plan generator
  -> vectorized execution
  -> profiling / results
```

Important entry points:

- `src/parser/parser.cpp`: SQL parsing entry.
- `src/planner/planner.cpp`: statement planning.
- `src/main/client_context.cpp`: query coordination.
- `src/optimizer/optimizer.cpp`: optimizer pass orchestration.
- `src/execution/physical_plan/physical_plan_generator.cpp`: logical-to-physical lowering.
- `src/parallel/executor.cpp`: execution orchestration.

## Optimizer Shape

`Optimizer::RunBuiltInOptimizers()` runs expression rewrites, filter movement, CTE handling, join ordering, and later cleanup passes. The join ordering pass is invoked as:

```cpp
JoinOrderOptimizer optimizer(context);
plan = optimizer.Optimize(std::move(plan));
```

ADL-OPT should treat the existing join-order optimizer as the baseline and source of implementation truth.

## Join-Order Components

The relevant DuckDB join-order subsystem lives under `src/optimizer/join_order/`.

- `join_order_optimizer.cpp`: pass entry and reconstruction handoff.
- `query_graph_manager.cpp`: extracts reorderable relations and join edges.
- `query_graph.cpp`: stores join graph structure.
- `relation_manager.cpp`: tracks relation sets and statistics.
- `cardinality_estimator.cpp`: estimates relation and join cardinalities.
- `cost_model.cpp`: scores plans.
- `plan_enumerator.cpp`: enumerates and solves join order.

These components correspond naturally to the ADL-OPT and NeuSO vocabulary:

- DuckDB relation set ~= ADL-OPT state / NeuSO subquery.
- DuckDB join edge ~= ADL-OPT transition precondition.
- DuckDB plan enumerator ~= transition path search.
- DuckDB cost/cardinality estimates ~= model input and baseline labels.

## ADL-OPT v0 Boundary

ADL-OPT v0 does not change DuckDB public APIs or optimizer behavior. It creates an offline harness around DuckDB:

1. Read TPC-H query text.
2. Parse or manually normalize the join graph.
3. Enumerate connected join states and valid transitions.
4. Generate SQL variants that try to fix join order through explicit parentheses and `disabled_optimizers='join_order'`.
5. Run DuckDB and collect `EXPLAIN`, profiling JSON, row counts, and result checksums.
6. Write JSONL artifacts for later model training and analysis.

This keeps the first research loop reproducible while avoiding early C++ optimizer risk.

## R5 Export-Only Boundary

R5 adds one narrow in-tree debug path near the join-order optimizer:

- It can export IKKBZ-style linear order candidates for `n >= 12` large joins.
- It is controlled by local debug settings and writes optional JSON metadata.
- It does not change DuckDB public APIs.
- It does not replace DuckDB's chosen join plan.
- It does not support non-inner or non-regular join graph boundaries yet.

Usage details are in `docs/design-docs/ikkbz-linearization-export-usage.md`.

## Future Integration Boundary

After the offline harness proves useful, the next implementation plan may introduce an experimental in-tree hook near the join-order enumerator. The likely integration point is not a global optimizer extension alone, because optimizer extensions run before or after built-in optimizers and do not directly replace `PlanEnumerator` decisions.

The future interface should be designed only after v0 has:

- Stable query graph extraction.
- Stable state and transition JSONL artifacts.
- Evidence that fixed join orders are respected by DuckDB.
- A baseline model or heuristic that beats at least one non-default baseline on TPC-H small scale.
