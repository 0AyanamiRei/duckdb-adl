# 0003 Large-Join Endpoint Harness

English TL;DR: Narrow ADL-OPT to DuckDB's n>12 large-join approximate path and add a static JOB/IMDB harness for linear-order endpoint-append decisions.

Updated: 2026-05-06

Key terms: large join, JOB/IMDB, n>12, linear order, endpoint append, NeuSO

## Goal

把 ADL-OPT 的下一阶段边界收窄到 DuckDB 已经不跑 exact DPhyp 的大 join 区间：

- `n <= 12` 继续使用 DuckDB exact DPhyp，不插手。
- `n > 12` 才研究 ADL-OPT 是否能改善 approximate join-order decision。
- 暂不实现 Neumann-style 线性化算法；它单独开 issue 讨论：<https://github.com/0AyanamiRei/duckdb-adl/issues/2>。
- 先用 fixture linear order 验证 JSON 交换接口和线性化后的 endpoint append 决策。

## Non-Goals

- 不修改 DuckDB C++ optimizer 行为。
- 不把模型放进 DuckDB 内核。
- 不实现真正的 large-join linearization algorithm。
- 不做 JOB/IMDB 数据加载和性能执行。

## Implementation Tasks

- 新增 `scripts/adl_opt/offline_large_join_harness.py`，解析 JOB/IMDB 29/28/33 系列 SQL。
- 产出 large-join query graph、fixture linear order、linear interval state、endpoint transition、endpoint path 和 run-result placeholder。
- 更新设计文档，将 NeuSO-style transition 明确限定为“线性化后的 endpoint append”，不是替代 DuckDB DPhyp。
- 更新 schema，加入 `linear_order.jsonl` 和 `endpoint_path.jsonl`。
- 更新 README 和 benchmark protocol，说明 TPC-H 不足以展示 `n > 12` 优势，JOB/IMDB 是下一阶段主验证方向。

## Validation

静态验证，不需要 DuckDB binary：

```bash
python3 -m py_compile scripts/adl_opt/offline_large_join_harness.py
python3 scripts/adl_opt/offline_large_join_harness.py \
  --output /tmp/adl-opt-large-static \
  --queries 29a 29b 29c 28a 28b 28c 33a 33b 33c \
  --random-paths 5
```

验收标准：

- `summary.json` 中 `query_count=9`。
- 所有查询 `large_join_candidate=true`。
- relation count 范围是 14 到 17。
- `linear_order.jsonl` 每个 query 一条 fixture linear order。
- `endpoint_path.jsonl` 至少包含一个有效的 `adl_opt_endpoint_fixture` path。
- `transition.jsonl` 同时记录 valid/invalid endpoint transition，供后续模型和失败分析使用。

## Pause Criteria

如果 JOB/IMDB SQL 解析不能稳定抽出 alias、join edge 和 filter，先暂停进入 DuckDB C++ 接口设计。真正的线性化算法、DuckDB 导出 estimated cardinality/cost、以及读取 ADL-OPT 决策都进入后续计划。
