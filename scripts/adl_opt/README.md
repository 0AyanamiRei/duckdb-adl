# ADL-OPT Offline Harness

English TL;DR: Local runners and smoke scripts for ADL-OPT TPC-H, JOB/IMDB, and R5 IKKBZ linearization export checks.

Updated: 2026-05-10

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

Classic JOB/IMDB executable benchmark runner. This path expects an existing
IMDB DuckDB database and verifies the needed tables before running:

```bash
python3 scripts/adl_opt/job_benchmark_runner.py \
  --duckdb ./build/reldebug/duckdb \
  --database /path/to/imdb.duckdb \
  --output /tmp/adl-opt-runs \
  --run-id job_r1_smoke \
  --queries 29a 29b 29c 28a 28b 28c 33a 33b 33c \
  --execute \
  --baseline-kinds duckdb_default \
  --threads 1 \
  --temp-directory /home/refrain/data/adl-opt/job-imdb/tmp \
  --max-temp-directory-size 8GB \
  --max-memory 4GB \
  --warmup-runs 1 \
  --measure-runs 3
```

`job_benchmark_runner.py` is the first JOB/IMDB runner that measures real
DuckDB behavior. It keeps SQL-to-plan time separate from physical execution
time using DuckDB detailed profiling. `plan_result.jsonl` records parser,
planner, optimizer, join-order optimizer, and physical planner timing.
`run_result.jsonl` records physical execution samples computed as profile
latency minus profiled plan phases, plus row counts, checksums,
P50/P95/P99/max, speedup, and sampled-oracle regret. External DuckDB process
wall-clock is kept only as a diagnostic field. The runner only selects classic
JOB queries whose parsed graph is a large connected regular inner pair graph.
JOBLight is not part of this stage.

Use `--baseline-kinds duckdb_default` for a clean DuckDB default optimizer run.
The default `--baseline-kinds all` keeps the full runner behavior and emits the
other configured validation/baseline variants as well.
`sql_original` is a reference baseline: it may fail or timeout when join-order
optimization is disabled, and those cases are recorded rather than blocking the
whole run.

Executable runs set DuckDB's `temp_directory`, `max_temp_directory_size`, and
`max_memory` before each `EXPLAIN` or query execution. The defaults are
`<database>.tmp-safe`, `8GB`, and `4GB`; override them explicitly on small local
disks so spill files cannot grow until WSL becomes unusable.

For valid `random_endpoint` variants, the runner rewrites the comma-style JOB
`FROM` list into an explicit `JOIN ... ON ...` tree and sets
`disabled_optimizers='join_order'`. IKKBZ top-1 remains export-only. The
`adl_opt_applied` variant sends the regular large join graph to the NeuSO
runtime sidecar and applies the returned relation order inside DuckDB's
join-order pass.

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

NeuSO runtime bridge file-driven regression:

```bash
PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py \
  --mode regression \
  --duckdb build/reldebug/duckdb \
  --testdata-dir scripts/adl_opt/testdata/neuso_runtime_bridge \
  --device cpu
```

Regression cases store the SQL scenario and stable expected sidecar response
under `scripts/adl_opt/testdata/neuso_runtime_bridge/`. The runner configures
the sidecar through DuckDB CLI `-cmd`, enables `adl_neuso_runtime_enabled` to
pre-start it, enables the R5 linearizer so the NeuSO request includes
`base_linear_order`, executes the workload SQL through DuckDB, reads the sidecar
trace, compares normalized responses, and deliberately ignores dynamic request
id, graph hash, and latency fields.

NeuSO runtime bridge SQL-to-DuckDB sidecar smoke:

```bash
PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py \
  --mode duckdb-runtime \
  --duckdb build/reldebug/duckdb \
  --database /tmp/neuso-runtime-smoke.duckdb \
  --output /tmp/neuso-runtime-smoke
```

This path lets DuckDB auto-manage the Python sidecar. The runner pre-starts the
sidecar by setting `adl_neuso_runtime_enabled=true` through CLI `-cmd` before the
workload SQL reaches the optimizer; join-order optimization then sends and
validates the NeuSO request/response, then applies the returned order as an
experimental left-deep join plan. The runner writes `duckdb_runtime_trace.json`
under the output directory so the actual DuckDB request and sidecar response can
be reviewed.

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
- `workload.jsonl` for JOB executable benchmark runs
- `variant.jsonl` for JOB executable benchmark runs
- `plan_result.jsonl` for JOB executable benchmark runs
- `correctness.jsonl` for JOB executable benchmark runs
- `linear_order.jsonl` for large-join endpoint runs
- `endpoint_path.jsonl` for large-join endpoint runs
- `traces/` and `profiles/` for executable JOB runs
- `summary.json`
- `summary.md`

The schema is documented in `docs/design-docs/feature-and-label-schema.md`.

`run_result.jsonl` stores physical execution latency samples, P50/P95/P99/max latency, row count, order-independent checksum, `EXPLAIN` hash, and detailed-profile optimizer/execution timings. For JOB/IMDB, external DuckDB process wall-clock is diagnostic only.

The large-join Python runner is still static. R5 adds a separate DuckDB kernel
export path that can write IKKBZ-style linearization candidates for real
large-join plans. The NeuSO runtime path can now consume those candidates and
apply the sidecar response to DuckDB's chosen join plan when the experimental
settings are enabled.

The JOB executable runner is deliberately separate from `offline_large_join_harness.py`:
the static harness remains useful for endpoint-path artifact design, while the
JOB runner is the place for correctness, SQL-to-plan latency, and physical-plan
execution latency measurements.
