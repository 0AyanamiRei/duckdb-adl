# ADL-OPT Offline Harness

English TL;DR: Local runners and smoke scripts for ADL-OPT TPC-H, JOB/IMDB, and R5 IKKBZ linearization export checks.

Updated: 2026-05-07

Key terms: ADL-OPT, offline runner, TPC-H, JOB/IMDB, large join, IKKBZ, JSONL

## Usage

Static artifact generation, no DuckDB binary required:

```bash
python3 scripts/adl_opt/offline_tpch_harness.py --output /tmp/adl-opt-run --queries q03 q05 q08
```

Large-join JOB/IMDB static artifact generation, no DuckDB binary required:

```bash
python3 scripts/adl_opt/offline_large_join_harness.py \
  --output /tmp/adl-opt-large-static \
  --queries 29a 29b 29c 28a 28b 28c 33a 33b 33c \
  --random-paths 5
```

Build DuckDB with the TPC-H extension while using roughly 75% CPU:

```bash
CPU_COUNT=$(nproc 2>/dev/null || sysctl -n hw.ncpu)
BUILD_JOBS=$(( CPU_COUNT * 75 / 100 ))
[ "$BUILD_JOBS" -lt 1 ] && BUILD_JOBS=1
CMAKE_BUILD_PARALLEL_LEVEL=$BUILD_JOBS BUILD_TPCH=1 make reldebug
```

R5 DuckDB kernel IKKBZ linearization export smoke:

```bash
./build/reldebug/duckdb /tmp/adl-opt-r5-smoke.duckdb \
  < scripts/adl_opt/r5_ikkbz_linearization_smoke.sql

python3 -m json.tool /tmp/adl-opt-linearization.json | less
```

The R5 settings, JSON fields, and test expectations are documented in `docs/design-docs/ikkbz-linearization-export-usage.md`.

R1 executable smoke with a DuckDB CLI/binary available:

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

The execute path assumes the TPC-H extension is available to the binary. If data is not present, the script tries:

```sql
LOAD tpch;
CALL dbgen(sf=0.01);
```

The database is reused when TPC-H tables already exist. Add `--force-reload` to regenerate it.

## Outputs

- `query_graph.jsonl`
- `state.jsonl`
- `transition.jsonl`
- `decision.jsonl`
- `run_result.jsonl`
- `linear_order.jsonl` for large-join endpoint runs
- `endpoint_path.jsonl` for large-join endpoint runs
- `summary.json`
- `summary.md`

The schema is documented in `docs/design-docs/feature-and-label-schema.md`.

`run_result.jsonl` stores latency samples, P50/P95 latency, row count, order-independent checksum, `EXPLAIN` hash, and optional profiling-derived optimizer/execution times.

The large-join Python runner is still static. R5 adds a separate DuckDB kernel export-only path that can write IKKBZ-style linearization candidates for real large-join plans, but it still does not apply those candidates to DuckDB's chosen plan.
