# ADL-OPT Research Spec

English TL;DR: ADL-OPT starts with TPC-H smoke runs, then evaluates n>12 large joins on executable classic JOB/IMDB runs.

Updated: 2026-05-10

Key terms: research spec, DuckDB, TPC-H, JOB/IMDB, large join, endpoint append

## Problem

Traditional query optimizers rely on cost and cardinality estimates that can be wrong for complex workloads. ADL-OPT studies whether a lightweight learned component can choose better join-order transitions while preserving DuckDB as the trusted parser, optimizer baseline, and execution engine.

当前研究边界进一步收窄：DuckDB 对 `n <= 12` 的 join 已经使用 exact DPhyp，这一段优先相信原生优化器。ADL-OPT 后续主要研究 `n > 12` 的 large join approximate 区间。

## v0 Capability

ADL-OPT v0 must produce a reproducible offline dataset for learning and evaluating join-order decisions:

- Extract or define the join graph for selected TPC-H queries.
- Enumerate connected states and valid transitions.
- Generate comparable SQL variants for candidate join paths.
- Run DuckDB with profiling enabled.
- Validate correctness against DuckDB default.
- Store all observations in JSONL files.

Large-join capability adds:

- Parse JOB/IMDB n>12 SQL into query graph JSON.
- Record fixture linear orders before the real linearization algorithm exists.
- Record endpoint-append states, transitions, paths, and decisions over a linear order.
- Run classic JOB/IMDB executable benchmark variants against DuckDB.
- Measure SQL-to-plan latency separately from physical-plan execution latency.
- Keep first-stage model decisions outside DuckDB in JSON artifacts.

## Workload

Initial workload:

- TPC-H SF 0.1 preferred.
- TPC-H SF 0.01 fallback if local setup is too slow.
- First queries: Q3, Q5, Q8, Q9, Q10.
- Minimum acceptance subset: Q3, Q5, Q8.

The staged dataset-size and benchmark protocol is defined in `docs/design-docs/experiment-scale-and-benchmark-protocol.md`. In short, TPC-H SF 0.01 is the minimal smoke scale, TPC-H SF 0.1 is the routine development scale, TPC-H SF 1 is local validation scale, and classic JOB/IMDB is the main thesis benchmark for n>12 large joins.

Large-join first queries:

- JOB/IMDB 29a, 29b, 29c: about 17 relations.
- JOB/IMDB 28a, 28b, 28c: about 14 relations.
- JOB/IMDB 33a, 33b, 33c: about 14 relations.

JOBLight is not part of the first-stage acceptance benchmark. It can be revisited after the classic JOB executable loop is stable.

## Baselines

Required baselines:

- DuckDB default optimizer.
- SQL original join order.
- Cardinality heuristic order.
- Five random valid connected orders per query.
- Sampled oracle best order when enough samples exist.

Large-join baselines:

- DuckDB default optimizer.
- DuckDB current approximate greedy path.
- SQL original order.
- IKKBZ top-1 export validation, where DuckDB still chooses the final plan.
- NeuSO runtime validation, where DuckDB validates the sidecar response but still chooses the final plan.
- Fixture linear order plus ADL-OPT endpoint path.
- Random endpoint path over the same linear order, applied as an explicit join tree when valid.

## Data Contract

The v0 harness writes:

- `query_graph.jsonl`
- `state.jsonl`
- `transition.jsonl`
- `run_result.jsonl`
- `decision.jsonl`

The large-join harness also writes:

- `linear_order.jsonl`
- `endpoint_path.jsonl`

The executable JOB/IMDB benchmark runner writes a run-id directory:

- `run_config.json`
- `workload.jsonl`
- `query_graph.jsonl`
- `variant.jsonl`
- `plan_result.jsonl`
- `run_result.jsonl`
- `correctness.jsonl`
- `summary.json`
- `summary.md`
- `traces/`
- `profiles/`

The canonical schema is in `docs/design-docs/feature-and-label-schema.md`.

The TPC-H runner is `scripts/adl_opt/offline_tpch_harness.py`. The JOB/IMDB static large-join runner is `scripts/adl_opt/offline_large_join_harness.py`. The executable classic JOB/IMDB benchmark runner is `scripts/adl_opt/job_benchmark_runner.py`.

## Acceptance Criteria

The first complete experiment is accepted when:

- Q3, Q5, and Q8 each have the required baselines.
- Every comparable run has row count and checksum.
- Every fixed-order run has plan-control evidence from `EXPLAIN`.
- The summary reports speedup/regret, P50/P95 latency, optimizer time, execution time, and failure counts.

The first classic JOB/IMDB benchmark is accepted when:

- 29a/29b/29c, 28a/28b/28c, and 33a/33b/33c are parsed and filtered through the `n >= 12` regular inner pair graph gate.
- DuckDB default has row count and checksum for each selected query.
- Each executable variant reports SQL-to-plan latency P50/P95/P99/max from DuckDB detailed profiling.
- Each executable variant reports physical execution latency P50/P95/P99/max from DuckDB detailed profiling.
- Correctness failures and timeouts are explicitly counted.
- Execution failures and failed variants are explicitly counted.
- Speedup/regret only use correct, non-timeout runs.

## Out of Scope

- Online inference inside DuckDB.
- C++ optimizer changes.
- Full TPC-H or TPC-DS coverage.
- JOBLight as a first-stage benchmark adapter.
- Outer join, ASOF, MARK, SINGLE, dependent/delim join, and complex correlated-subquery reorder as first-stage workloads.
- Production-grade model serving.
- Frontend dashboard.
- Implementing the real large-join linearization algorithm in the first endpoint harness.
