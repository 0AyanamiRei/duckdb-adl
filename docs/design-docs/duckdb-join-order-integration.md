# DuckDB Join-Order Integration Notes

English TL;DR: v0 is offline. Future ADL-OPT integration should target DuckDB's n>12 approximate join-order path, leaving exact DPhyp for n<=12 unchanged.

Updated: 2026-05-06

Key terms: DuckDB, join order optimizer, PlanEnumerator, QueryGraphManager, large join, JSON exchange

## 当前接入策略

v0 只做离线 harness：

- 使用 DuckDB 生成 TPC-H 数据和执行 SQL。
- 使用 explicit parenthesized joins 和 `disabled_optimizers='join_order'` 尝试控制 join order。
- 使用 `EXPLAIN` 和 profiling 验证计划与运行时。
- 不改 `src/optimizer/`。

R3 之后的 large-join 方向收窄为：

- DuckDB `PlanEnumerator` 当前在 relation count `>= 12` 时直接进入 approximate greedy pair merge。
- `n <= 12` 的 exact DPhyp 不作为 ADL-OPT 优化目标。
- ADL-OPT 第一版只通过 JSON 文件交换信息，不把模型放进 DuckDB 内核。
- 线性化算法暂不实现；先用 fixture linear order 验证 endpoint append 决策。

## 相关 DuckDB 文件

- `src/optimizer/optimizer.cpp`：内置 optimizer pass 编排。
- `src/optimizer/join_order/join_order_optimizer.cpp`：join-order pass 入口。
- `src/optimizer/join_order/query_graph_manager.cpp`：抽取可重排 relation 和 join edge。
- `src/optimizer/join_order/plan_enumerator.cpp`：枚举和求解 join order。
- `src/optimizer/join_order/cost_model.cpp`：计划代价模型。
- `src/optimizer/join_order/cardinality_estimator.cpp`：基数估计。
- `src/include/duckdb/optimizer/optimizer_extension.hpp`：pre/post optimizer extension API。

## 为什么 v0 不直接用 Optimizer Extension

DuckDB optimizer extension 可以在内置 optimizer 之前或之后修改 logical plan。它适合：

- 收集/标注 logical plan。
- 做额外 rewrite。
- 验证外部 pass 的可加载性。

但 ADL-OPT 未来如果要真正替代 join-order decision，最自然的位置在 join-order enumerator 内部。仅靠 extension 难以直接替换 `PlanEnumerator` 的搜索策略。

## 未来 in-tree 方向

v0 之后可以规划一个 experimental large-join bridge：

- 从 `QueryGraphManager` 读取 relation ids、aliases、join edges、filters、estimated cardinality 和 cost feature。
- 将 large-join graph 导出为 JSON。
- 从外部 ADL-OPT JSON 读取 linear order 和 endpoint append path。
- 只在 `n > 12` approximate path 中尝试使用 ADL-OPT path。
- 若 JSON 缺失、无效或 relation count 不满足阈值，回退 DuckDB 当前 enumerator。
- 用 setting 或 build flag 控制启用。

这个方向必须另起执行计划，因为它会改变 DuckDB optimizer 行为。

## JSON 交换边界

第一版不新增 DuckDB public API。推荐用调试 setting 或本地实验 build flag 指定：

- export path：DuckDB 写出 query graph JSON。
- decision path：DuckDB 读取 ADL-OPT 输出的 order/path JSON。
- mode：`off`、`export_only`、`apply_if_valid`。

JSON 失败必须 fail closed：记录错误，回退 DuckDB 当前策略，而不是生成不完整计划。

## v0 Plan-Control 注意点

固定 join order 的 SQL variant 必须满足：

- join graph connected。
- 每一步只追加一个相邻 relation。
- SQL 结果与 default 一致。
- `EXPLAIN` 显示的 join tree 与预期 path 一致，或标记为 uncontrolled。

如果 DuckDB 仍然重排 join tree，该 variant 不能用于 speedup 结论。
