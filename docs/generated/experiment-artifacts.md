# Generated: Experiment Artifacts

English TL;DR: Placeholder index for ADL-OPT generated experiment outputs and summaries.

Updated: 2026-05-06

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
```

Static generation command:

```bash
python3 scripts/adl_opt/offline_tpch_harness.py --output /tmp/adl-opt-run --queries q03 q05 q08
```

Do not commit large generated databases, model checkpoints, or raw profiling dumps unless a plan explicitly says they are curated fixtures.

## Summary Metrics

Each `summary.md` should include:

- Query count.
- Variant count.
- Correctness failures.
- Plan-control failures.
- Timeout count.
- P50/P95 latency.
- Optimizer time and execution time.
- Speedup/regret against DuckDB default and sampled oracle where available.
