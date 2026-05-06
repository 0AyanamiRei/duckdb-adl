# Feature and Label Schema

English TL;DR: ADL-OPT v0 standardizes JSONL artifacts before training models, so experiments can be replayed and compared.

Updated: 2026-05-06

Key terms: JSONL, query graph, state, transition, decision, run result, feature, label

## 文件约定

v0 标准输出使用 JSONL。每行一个对象，便于追加、合并和流式处理。

Required artifact files:

- `query_graph.jsonl`
- `state.jsonl`
- `transition.jsonl`
- `run_result.jsonl`
- `decision.jsonl`

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

## `run_result.jsonl`

记录 SQL variant 的执行结果。

Required fields:

- `query_id`: string.
- `variant_id`: string.
- `baseline_kind`: `duckdb_default`, `sql_original`, `cardinality_heuristic`, `random_valid`, or `sampled_oracle`.
- `join_path`: array of aliases.
- `sql_hash`: string.
- `explain_hash`: string or null.
- `plan_control_valid`: boolean.
- `correct`: boolean.
- `row_count`: number or null.
- `result_checksum`: string or null.
- `latency_ms`: number or null.
- `optimizer_time_ms`: number or null.
- `execution_time_ms`: number or null.
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

## Label 规则

- correctness label 必须基于 DuckDB default 的 row count 和 checksum。
- runtime label 必须标记是否 timeout。
- speedup/regret 只在 `correct=true` 且 `plan_control_valid=true` 的 run 上计算。
