# ADL-OPT Quality Score

English TL;DR: Quality is measured by reproducibility, correctness, plan control, useful metrics, and clear research traceability.

Updated: 2026-05-06

Key terms: quality score, reproducibility, correctness, plan control, regret, speedup

## Quality Dimensions

Use this rubric for ADL-OPT v0 artifacts and experiments.

| Dimension | Target | Minimum acceptable evidence |
| --- | --- | --- |
| Documentation | Agent-readable research map | `AGENTS.md` links exist and each ADL-OPT doc has English TL;DR, updated date, and key terms |
| Correctness | SQL variants preserve answers | Row count and checksum match DuckDB default for each variant |
| Plan control | Fixed-order SQL is respected | `EXPLAIN` output hash and join-tree notes recorded per variant |
| Reproducibility | Runs can be replayed | Commands, DuckDB build, scale factor, query id, seed, and output paths recorded |
| Metrics | Results support comparison | Latency, optimizer time, execution time, speedup/regret, P50/P95, failure counts |
| Research value | Data maps to ADL-OPT ideas | JSONL includes query graph, connected states, transitions, decisions, and run results |

## v0 Acceptance Bar

- TPC-H Q3, Q5, and Q8 each have default, SQL original order, 5 random valid orders, and 1 cardinality heuristic order.
- Every run records `query_graph.jsonl`, `state.jsonl`, `transition.jsonl`, `run_result.jsonl`, and `decision.jsonl`.
- Every SQL variant either passes correctness checks or records a structured failure reason.
- A summary reports baseline comparisons and the number of invalid or uncontrolled plans.

## Scoring

Use a 0-2 score per dimension:

- 0: absent or not trustworthy.
- 1: present but incomplete, manual, or partially validated.
- 2: automated or clearly reproducible with enough metadata.

The v0 harness is considered useful when the total score is at least 9 out of 12 and correctness is 2.
