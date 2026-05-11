# Feature and Label Schema

English TL;DR: ADL-OPT standardizes JSONL experiment artifacts and the R5 JSON export shape for IKKBZ large-join linearization.

Updated: 2026-05-10

Key terms: JSONL, query graph, state, transition, decision, run result, IKKBZ, feature, label

## 文件约定

v0 标准输出使用 JSONL。每行一个对象，便于追加、合并和流式处理。

Required artifact files:

- `query_graph.jsonl`
- `state.jsonl`
- `transition.jsonl`
- `run_result.jsonl`
- `decision.jsonl`

Large-join endpoint experiments also write:

- `linear_order.jsonl`
- `endpoint_path.jsonl`

Executable JOB/IMDB benchmark runs also write:

- `workload.jsonl`
- `variant.jsonl`
- `plan_result.jsonl`
- `correctness.jsonl`
- `traces/`
- `profiles/`

TPC-H R1/R2 和 JOB/IMDB runner 共享同一个“JSONL 一行一个观测”的习惯，但字段不会强行完全相同。TPC-H 旧 runner 保留 `latency_*` 字段；JOB/IMDB executable runner 使用更清楚的 `plan_latency_*` 和 `execution_latency_*` 字段，把“SQL 到物理 plan 的开销”和“物理 plan 实际执行质量”分开。

## `workload.jsonl`

记录一次 benchmark run 的 workload 输入和筛选结果。

Required fields:

- `query_id`: string, for example `job_29a`.
- `workload`: string, for example `job_imdb`.
- `workload_query`: original benchmark query name, for example `29a`.
- `sql_file`: repository-relative SQL path.
- `selected`: boolean, whether this query entered the executable benchmark set.
- `selection_reason`: string, for example `selected_large_regular_inner_pair_graph`, `below_large_join_threshold`, `not_regular_inner_pair_graph`, or `disconnected_join_graph`.
- `relation_count`: number.
- `join_edge_count`: number.
- `large_join_threshold`: number.
- `regular_inner_pair_graph`: boolean.
- `connected_join_graph`: boolean.
- `sql_hash`: stable hash of the SQL text.

`job_benchmark_runner.py` 第一阶段只选择 classic JOB/IMDB 中 `relation_count >= 12`、能被静态解析为 regular inner pair graph、且 join graph 连通的查询。JOBLight 不进入当前 schema 和验收范围。

## `query_graph.jsonl`

记录 SQL join graph。

Required fields:

- `query_id`: string, for example `tpch_q03`.
- `workload`: string, for example `tpch`.
- `scale_factor`: number.
- `aliases`: array of `{ "alias": "...", "table": "..." }`.
- `edges`: array of `{ "left": "...", "right": "...", "predicate": "..." }`.
- `filters`: array of SQL filter strings not used as join edges.
- `sql_hash`: stable hash of normalized SQL.

Large-join rows may also include `relation_count`, `join_edge_count`, `large_join_threshold`, `large_join_candidate`, `estimated_cardinality_available`, and `estimated_cost_available`.

JOB executable rows may also include `workload_query`, `regular_inner_pair_graph`, and `connected_join_graph`.

## `variant.jsonl`

记录一次 executable benchmark 中要比较的 variant。

Required fields:

- `query_id`: string.
- `variant_id`: stable id, for example `job_29a:duckdb_default`.
- `baseline_kind`: `duckdb_default`, `sql_original`, `ikkbz_top1_export`, `neuso_runtime_validate`, or `random_endpoint`.
- `executable`: boolean. `false` means this variant is recorded for comparison/design but not applied to DuckDB yet.
- `settings_kind`: string describing the DuckDB settings block.
- `join_path`: array of aliases, empty when DuckDB still chooses the plan.
- `endpoint_sides`: array of `left`/`right` choices for endpoint-path variants.
- `seed`: number or null.
- `note`: string or null.
- `path_valid`: boolean or null.
- `path_failure_reason`: string or null.
- `covered_alias_count`: number or null.

第一版 IKKBZ/NeuSO variant 是“验证/导出路径”，不是“强制 DuckDB 使用该 join order”。valid random endpoint path 会被改写成显式 `JOIN ... ON ...` tree，并配合 `disabled_optimizers='join_order'` 作为可执行 baseline；后续如果要应用 IKKBZ/NeuSO 选择，还需要 SQL rewrite 或内核 hook。

## `plan_result.jsonl`

记录 plan 阶段。JOB/IMDB runner 使用 DuckDB detailed profiling，而不是外部 `time` 或每条 SQL 的子进程 wall-clock。`plan_latency_*` 在 JOB/IMDB 中表示 SQL parser、planner、optimizer、physical planner 阶段的合计时间；外部 DuckDB process wall-clock 只作为诊断字段。`EXPLAIN` 每个 variant 只执行一次，用于 `explain_hash` 和 plan 可生成性检查。

Required fields:

- `query_id`: string.
- `variant_id`: string.
- `baseline_kind`: string.
- `measurement_source`: `duckdb_detailed_profile`.
- `plan_latency_samples_ms`: array of numbers.
- `plan_latency_p50_ms`: number or null.
- `plan_latency_p95_ms`: number or null.
- `plan_latency_p99_ms`: number or null.
- `plan_latency_max_ms`: number or null.
- `qo_plan_time_samples_ms`: array of numbers, same semantic surface as `plan_latency_samples_ms` for JOB/IMDB.
- `duckdb_wall_time_samples_ms`: diagnostic process/session wall-clock samples, not the formal benchmark metric.
- `parser_time_p50_ms`: number or null.
- `planner_time_p50_ms`: number or null.
- `planner_binding_time_p50_ms`: number or null.
- `optimizer_time_p50_ms`: number or null.
- `join_order_optimizer_time_p50_ms`: number or null.
- `physical_planner_time_p50_ms`: number or null.
- `explain_hash`: string or null.
- `physical_plan_available`: boolean.
- `failure_reason`: string or null.

Optional fields:

- `sidecar_latency_ms`: number or null.
- `model_latency_ms`: number or null.
- `optimizer_time_ms`: number or null.

## `linear_order.jsonl`

记录 large-join 线性化结果或 fixture 线性顺序。

Required fields:

- `query_id`: string.
- `linear_order_id`: stable id for this order within the query.
- `source`: string, for example `fixture_folded_connected_degree`.
- `true_linearization_algorithm`: boolean.
- `linear_order`: array of aliases.
- `relation_count`: number.
- `updated`: string.

第一版 fixture 不是 Neumann-style 线性化算法，只用于验证 JSON 接口和 endpoint append 决策。

R5 DuckDB kernel export uses a richer JSON document when `adl_linearization_output` is set:

- `version`: export schema version.
- `status`: `ok`, `skipped_not_large_join`, `unsupported`, or `export_error` in EXPLAIN summary. `unsupported` means the current reorderable subgraph did not match R5's regular inner pair graph contract.
- `relation_count`, `large_join_threshold`, `k_requested`, `k_emitted`.
- `relations`: relation id, internal label, base cardinality.
- `edges`: relation ids, join type, filter index, estimated pair cardinality, selectivity, Cout rank, MST marker.
- `linear_orders`: top-k root candidates. Each row includes compact consumer fields `linear_order_id` and `order`, plus detailed metadata such as relation id order, root relation id, score, and rank trace.
- `selected_order_id`: top-1 candidate id; it is not applied to the DuckDB plan.

Detailed usage and validation checks live in `docs/design-docs/ikkbz-linearization-export-usage.md`.

## `state.jsonl`

记录 connected join subset。

Required fields:

- `query_id`: string.
- `state_id`: stable id, for example aliases joined by `+`.
- `aliases`: sorted array of aliases.
- `is_connected`: boolean.
- `estimated_cardinality`: number or null.
- `actual_cardinality`: number or null.
- `feature_ref`: string or null.
- `source`: `full`, `partial`, `default_plan`, `random`, or `heuristic`.

Large-join endpoint states may also include:

- `linear_order_id`: string.
- `interval`: `[left_index, right_index]` over the linear order.
- `source`: `linearized_interval_fixture`.

## `transition.jsonl`

记录从一个 state 追加一个 relation 的动作。

Required fields:

- `query_id`: string.
- `transition_id`: stable id.
- `from_state_id`: string.
- `to_state_id`: string.
- `added_alias`: string.
- `valid`: boolean.
- `predicate_refs`: array of predicates connecting the added alias.
- `estimated_cost`: number or null.
- `runtime_cost`: number or null.
- `label_source`: `measured`, `estimated`, `sampled_oracle`, or `unknown`.

Large-join endpoint transitions may also include:

- `linear_order_id`: string.
- `interval`: `[left_index, right_index]`.
- `side`: `left` or `right`.
- `transition_kind`: `endpoint_append`.
- `current_aliases`: array of aliases already inside the interval.

## `run_result.jsonl`

记录 SQL variant 的执行结果。

Required fields:

- `query_id`: string.
- `variant_id`: string.
- `source_variant_id`: string or null, used when a row aliases or summarizes another variant.
- `baseline_kind`: `duckdb_default`, `sql_original`, `cardinality_heuristic`, `random_valid`, or `sampled_oracle`.
- `join_path`: array of aliases.
- `sql_hash`: string.
- `explain_hash`: string or null.
- `plan_control_valid`: boolean.
- `correct`: boolean.
- `row_count`: number or null.
- `result_checksum`: string or null.
- `latency_ms`: number or null.
- `latency_p50_ms`: number or null.
- `latency_p95_ms`: number or null.
- `latency_samples_ms`: array of numbers.
- `execution_latency_samples_ms`: array of numbers, used by executable JOB/IMDB benchmark runs.
- `execution_latency_p50_ms`: number or null.
- `execution_latency_p95_ms`: number or null.
- `execution_latency_p99_ms`: number or null.
- `execution_latency_max_ms`: number or null.
- `measurement_source`: `duckdb_detailed_profile` for JOB/IMDB executable runs.
- `duckdb_wall_time_samples_ms`: diagnostic process/session wall-clock samples, not the formal benchmark metric.
- `query_latency_samples_ms`: DuckDB profile query latency samples before subtracting plan phases.
- `qo_plan_time_samples_ms`: profiled SQL-to-plan time samples collected from the same measured executions.
- `execution_cpu_time_samples_ms`: DuckDB profile CPU-time samples.
- `optimizer_time_ms`: number or null.
- `join_order_optimizer_time_ms`: number or null.
- `qo_plan_time_ms`: number or null.
- `execution_time_ms`: number or null.
- `speedup_vs_default`: number or null.
- `regret_vs_sampled_oracle`: number or null.
- `timeout`: boolean.
- `failure_reason`: string or null.

For JOB/IMDB executable runs, `execution_latency_*` is the primary plan-quality proxy and means physical execution wall time derived from DuckDB detailed profiling: `query_latency_ms - qo_plan_time_ms`. It intentionally excludes CLI process startup and SQL-to-plan time. `duckdb_wall_time_samples_ms` is diagnostic only. `latency_*` remains valid for older TPC-H artifacts.

`sql_original` is a reference baseline. It may fail or timeout when join-order optimization is disabled; those rows keep `failure_reason`/`timeout`, do not block the benchmark run, and are excluded from successful speedup/regret calculations.

## `correctness.jsonl`

记录每个 executable variant 是否和 DuckDB default 的结果一致。

Required fields:

- `query_id`: string.
- `variant_id`: string.
- `baseline_kind`: string.
- `correct`: boolean.
- `row_count`: number or null.
- `result_checksum`: string or null.
- `reference_variant_id`: string or null.
- `failure_reason`: string or null.

## `decision.jsonl`

记录 heuristic/model 选择。

Required fields:

- `query_id`: string.
- `decision_id`: string.
- `policy`: string, for example `random_valid_seed_1`.
- `from_state_id`: string.
- `chosen_transition_id`: string.
- `candidate_transition_ids`: array of strings.
- `score`: number or null.
- `valid`: boolean.
- `seed`: number or null.

Large-join endpoint decisions may also include `linear_order_id`, `interval`, `candidate_aliases`, `chosen_alias`, and `side`.

## `endpoint_path.jsonl`

记录线性化后的 endpoint append path。

Required fields:

- `query_id`: string.
- `path_id`: string.
- `policy`: string, for example `adl_opt_endpoint_fixture` or `random_endpoint`.
- `linear_order_id`: string.
- `start_alias`: string.
- `join_path`: array of aliases in append order.
- `endpoint_sides`: array of `left`/`right` choices.
- `valid`: boolean.
- `failure_reason`: string or null.
- `covered_alias_count`: number.

## Label 规则

- correctness label 必须基于 DuckDB default 的 row count 和 checksum。
- runtime label 必须标记是否 timeout。
- speedup/regret 只在 `correct=true` 且 `plan_control_valid=true` 的 run 上计算。
- JOB/IMDB 主测评同时报告 plan latency 与 execution latency 的 P50/P95/P99/max。
