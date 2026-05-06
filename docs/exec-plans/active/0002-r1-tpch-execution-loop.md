# 0002 R1 TPC-H Execution Loop

English TL;DR: Turn the ADL-OPT offline TPC-H harness from static JSONL generation into a small executable R1 loop on TPC-H SF 0.01.

Updated: 2026-05-06

Key terms: R1, TPC-H, execution loop, checksum, profiling, CPU limit

## Goal

Build the first executable ADL-OPT experiment loop:

- Run TPC-H SF 0.01 for Q3, Q5, and Q8.
- Compare DuckDB default, SQL original order, cardinality heuristic order, and 5 random valid connected orders.
- Validate fixed-order variants with `EXPLAIN`.
- Record row count, order-independent checksum, latency samples, P50/P95 latency, and optional profiling metrics.
- Produce JSONL artifacts and summary files that can be used by later model and benchmark work.

## Non-Goals

- No DuckDB C++ optimizer changes.
- No online model inference.
- No JOB/IMDB workload in this plan.
- No TPC-H SF 0.1 or SF 1 requirement in this plan.

## Implementation Tasks

- Add R1 CLI defaults and execution controls to `scripts/adl_opt/offline_tpch_harness.py`.
- Add `--threads`, `--warmup-runs`, `--measure-runs`, `--plan-control-mode`, and `--force-reload`.
- Make TPC-H data generation idempotent unless `--force-reload` is supplied.
- Use session-level optimizer settings so fixed-order `EXPLAIN` and execution share the same plan-control mode.
- Use order-independent CSV checksums for correctness checks.
- Record latency samples and P50/P95 fields in `run_result.jsonl`.
- Parse profiling JSON opportunistically; profiling parse failure must not fail a run.
- Add sampled-oracle and regret summary values when comparable runs exist.
- Document R1 usage and the CPU-limited build command.

## CPU Build Limit

Compilation must use roughly 70-80% CPU. The default command uses 75%:

```bash
CPU_COUNT=$(nproc 2>/dev/null || sysctl -n hw.ncpu)
BUILD_JOBS=$(( CPU_COUNT * 75 / 100 ))
[ "$BUILD_JOBS" -lt 1 ] && BUILD_JOBS=1
CMAKE_BUILD_PARALLEL_LEVEL=$BUILD_JOBS BUILD_TPCH=1 make reldebug
```

## Validation

Static validation, no DuckDB binary required:

```bash
python3 -m py_compile scripts/adl_opt/offline_tpch_harness.py
python3 scripts/adl_opt/offline_tpch_harness.py \
  --output /tmp/adl-opt-static-check \
  --queries q03 q05 q08 \
  --random-orders 5
```

Executable R1 smoke after building DuckDB with TPC-H:

```bash
python3 scripts/adl_opt/offline_tpch_harness.py \
  --duckdb ./build/reldebug/duckdb \
  --database /tmp/adl-opt-r1-tpch.duckdb \
  --output /tmp/adl-opt-r1 \
  --queries q03 q05 q08 \
  --scale-factor 0.01 \
  --random-orders 5 \
  --execute \
  --threads 1 \
  --warmup-runs 1 \
  --measure-runs 5 \
  --timeout 120
```

Acceptance criteria:

- `summary.json` has `query_count=3`.
- Every query has DuckDB default, SQL original, cardinality heuristic, and 5 random valid variants.
- `correctness_failures=0`.
- `timeout_count=0`.
- Every executed variant has row count, result checksum, explain hash, and P50 latency.
- Profiling fields may be null, but profiling failure must not fail the run.

## Pause Criteria

Pause before expanding scope if fixed-order variants fail correctness, if `EXPLAIN` cannot run under the session settings, or if TPC-H SF 0.01 cannot complete under the default timeout.
