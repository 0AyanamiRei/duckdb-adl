# ADL-OPT Research Spec

English TL;DR: ADL-OPT v0 is an offline learned-optimizer research harness for DuckDB join-order decisions on small TPC-H workloads.

Updated: 2026-05-06

Key terms: research spec, DuckDB, TPC-H, join graph, connected state, transition, benchmark

## Problem

Traditional query optimizers rely on cost and cardinality estimates that can be wrong for complex workloads. ADL-OPT studies whether a lightweight learned component can choose better join-order transitions while preserving DuckDB as the trusted parser, optimizer baseline, and execution engine.

## v0 Capability

ADL-OPT v0 must produce a reproducible offline dataset for learning and evaluating join-order decisions:

- Extract or define the join graph for selected TPC-H queries.
- Enumerate connected states and valid transitions.
- Generate comparable SQL variants for candidate join paths.
- Run DuckDB with profiling enabled.
- Validate correctness against DuckDB default.
- Store all observations in JSONL files.

## Workload

Initial workload:

- TPC-H SF 0.1 preferred.
- TPC-H SF 0.01 fallback if local setup is too slow.
- First queries: Q3, Q5, Q8, Q9, Q10.
- Minimum acceptance subset: Q3, Q5, Q8.

The staged dataset-size and benchmark protocol is defined in `docs/design-docs/experiment-scale-and-benchmark-protocol.md`. In short, TPC-H SF 0.01 is the minimal smoke scale, TPC-H SF 0.1 is the routine development scale, TPC-H SF 1 is local validation scale, and JOB/IMDB is the main thesis benchmark after the harness is stable.

## Baselines

Required baselines:

- DuckDB default optimizer.
- SQL original join order.
- Cardinality heuristic order.
- Five random valid connected orders per query.
- Sampled oracle best order when enough samples exist.

## Data Contract

The v0 harness writes:

- `query_graph.jsonl`
- `state.jsonl`
- `transition.jsonl`
- `run_result.jsonl`
- `decision.jsonl`

The canonical schema is in `docs/design-docs/feature-and-label-schema.md`.

The minimal runner is `scripts/adl_opt/offline_tpch_harness.py`. It supports a static mode that only writes JSONL and an execute mode that uses a DuckDB binary when one is available.

## Acceptance Criteria

The first complete experiment is accepted when:

- Q3, Q5, and Q8 each have the required baselines.
- Every comparable run has row count and checksum.
- Every fixed-order run has plan-control evidence from `EXPLAIN`.
- The summary reports speedup/regret, P50/P95 latency, optimizer time, execution time, and failure counts.

## Out of Scope

- Online inference inside DuckDB.
- C++ optimizer changes.
- Full TPC-H or TPC-DS coverage.
- Outer join, ASOF, MARK, SINGLE, dependent/delim join, and complex correlated-subquery reorder as first-stage workloads.
- Production-grade model serving.
- Frontend dashboard.
