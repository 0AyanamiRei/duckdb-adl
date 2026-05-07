# Feature and Label Schema

English TL;DR: ADL-OPT standardizes JSONL experiment artifacts and the R5 JSON export shape for IKKBZ large-join linearization.

Updated: 2026-05-07

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

R5 DuckDB kernel export uses a richer JSON document when `debug_adl_opt_linearization_output` is set:

- `version`: export schema version.
- `status`: `ok`, `skipped_not_large_join`, `unsupported`, or `export_error` in EXPLAIN summary.
- `relation_count`, `large_join_threshold`, `k_requested`, `k_emitted`.
- `relations`: relation id, debug label, base cardinality.
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
- `optimizer_time_ms`: number or null.
- `execution_time_ms`: number or null.
- `speedup_vs_default`: number or null.
- `regret_vs_sampled_oracle`: number or null.
- `timeout`: boolean.
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
