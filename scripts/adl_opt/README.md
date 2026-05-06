# ADL-OPT Offline Harness

English TL;DR: Minimal local runner for generating ADL-OPT v0 JSONL artifacts from selected TPC-H join graphs.

Updated: 2026-05-06

Key terms: ADL-OPT, offline runner, TPC-H, JSONL, DuckDB CLI

## Usage

Static artifact generation, no DuckDB binary required:

```bash
python3 scripts/adl_opt/offline_tpch_harness.py --output /tmp/adl-opt-run --queries q03 q05 q08
```

Build DuckDB with the TPC-H extension while using roughly 75% CPU:

```bash
CPU_COUNT=$(nproc 2>/dev/null || sysctl -n hw.ncpu)
BUILD_JOBS=$(( CPU_COUNT * 75 / 100 ))
[ "$BUILD_JOBS" -lt 1 ] && BUILD_JOBS=1
CMAKE_BUILD_PARALLEL_LEVEL=$BUILD_JOBS BUILD_TPCH=1 make reldebug
```

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
- `summary.json`
- `summary.md`

The schema is documented in `docs/design-docs/feature-and-label-schema.md`.

`run_result.jsonl` stores latency samples, P50/P95 latency, row count, order-independent checksum, `EXPLAIN` hash, and optional profiling-derived optimizer/execution times.
