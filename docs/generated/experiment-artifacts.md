# Generated: Experiment Artifacts

English TL;DR: Placeholder index for ADL-OPT generated experiment outputs and summaries, including the classic JOB/IMDB executable run layout.

Updated: 2026-05-10

Key terms: generated, experiment artifacts, JSONL, summary, benchmark

## Status

No generated experiment outputs are committed yet.

## Expected Output Layout

Future local runs should write under an ignored output directory such as:

```text
adl-opt-runs/
  tpch_sf0_1/
    query_graph.jsonl
    state.jsonl
    transition.jsonl
    run_result.jsonl
    decision.jsonl
    summary.json
    summary.md
  job_r1_smoke/
    run_config.json
    workload.jsonl
    query_graph.jsonl
    variant.jsonl
    plan_result.jsonl
    run_result.jsonl
    correctness.jsonl
    summary.json
    summary.md
    traces/
    profiles/
```

Static generation command:

```bash
python3 scripts/adl_opt/offline_tpch_harness.py --output /tmp/adl-opt-run --queries q03 q05 q08
```

Classic JOB/IMDB executable benchmark command:

```bash
python3 scripts/adl_opt/job_benchmark_runner.py \
  --duckdb ./build/reldebug/duckdb \
  --database /path/to/imdb.duckdb \
  --output /tmp/adl-opt-runs \
  --run-id job_r1_smoke \
  --queries 29a 29b 29c 28a 28b 28c 33a 33b 33c \
  --execute \
  --threads 1 \
  --warmup-runs 1 \
  --measure-runs 7 \
  --plan-runs 7
```

The JOB runner expects a prepared classic IMDB DuckDB database. It does not download data, and JOBLight is not part of this stage. JOB/IMDB plan and execution latency fields come from DuckDB detailed profiling, not from per-query subprocess wall-clock. The runner keeps a diagnostic `duckdb_wall_time_samples_ms` field, but benchmark comparisons should use `plan_latency_*` and `execution_latency_*`.

Do not commit large generated databases, model checkpoints, or raw profiling dumps unless a plan explicitly says they are curated fixtures.

## Summary Metrics

Each `summary.md` should include:

- Query count.
- Variant count.
- Correctness failures.
- Plan-control failures.
- Timeout count.
- Plan latency P50/P95/P99/max from DuckDB detailed profiling.
- Physical execution latency P50/P95/P99/max from DuckDB detailed profiling.
- Optimizer time and execution time.
- Speedup/regret against DuckDB default and sampled oracle where available.
