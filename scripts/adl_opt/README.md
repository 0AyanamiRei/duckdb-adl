# ADL-OPT Offline Harness

English TL;DR: Minimal local runner for generating ADL-OPT v0 JSONL artifacts from selected TPC-H join graphs.

Updated: 2026-05-06

Key terms: ADL-OPT, offline runner, TPC-H, JSONL, DuckDB CLI

## Usage

Static artifact generation, no DuckDB binary required:

```bash
python3 scripts/adl_opt/offline_tpch_harness.py --output /tmp/adl-opt-run --queries q03 q05 q08
```

With a DuckDB CLI/binary available:

```bash
python3 scripts/adl_opt/offline_tpch_harness.py \
  --duckdb ./build/reldebug/duckdb \
  --database /tmp/adl-opt-tpch.duckdb \
  --output /tmp/adl-opt-run \
  --queries q03 q05 q08 \
  --execute
```

The execute path assumes the TPC-H extension is available to the binary. If data is not present, the script tries:

```sql
LOAD tpch;
CALL dbgen(sf=0.1);
```

## Outputs

- `query_graph.jsonl`
- `state.jsonl`
- `transition.jsonl`
- `decision.jsonl`
- `run_result.jsonl`
- `summary.json`
- `summary.md`

The schema is documented in `docs/design-docs/feature-and-label-schema.md`.
