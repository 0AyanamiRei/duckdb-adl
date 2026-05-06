# ADL-OPT Reliability

English TL;DR: The harness must fail closed: never claim a speedup unless the SQL result is correct and the intended plan-control strategy was validated.

Updated: 2026-05-06

Key terms: reliability, fail closed, profiling, result checksum, fixed join order, timeout

## Reliability Principles

- Correctness comes before speed.
- A run without a result checksum is not comparable.
- A fixed-order run without `EXPLAIN` evidence is not a controlled plan.
- Timeouts and unsupported query rewrites are data points, not silent skips.
- Random baselines must record their seed and selected transition path.

## Failure Modes

| Failure | Required handling |
| --- | --- |
| DuckDB build lacks TPC-H extension | Record setup failure and command needed to rebuild |
| TPC-H data generation is too slow | Fall back from SF 0.1 to SF 0.01 and record the change |
| Join-order disabling does not preserve intended order | Mark `plan_control_valid=false` and exclude from speedup claims |
| Query variant returns different result | Mark `correct=false`, keep artifact, exclude from performance comparison |
| Profiling JSON unavailable | Keep latency from outer timer if available, mark profiling fields null |
| Timeout | Record timeout duration and partial metadata |

## Reproducibility Metadata

Every run result should include:

- DuckDB git commit or working tree marker.
- Build path and command.
- Query id.
- Scale factor.
- Variant id and baseline kind.
- Random seed, if any.
- SQL text hash.
- `EXPLAIN` output hash.
- Result row count and checksum.
- Start time, end time, latency, and timeout status.

## Rollback Boundary

ADL-OPT v0 should not mutate DuckDB source behavior. If an experiment requires C++ optimizer changes, create a new execution plan before implementing it.
