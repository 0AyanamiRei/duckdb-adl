# DuckDB Join-Order Integration Notes

English TL;DR: ADL-OPT has an R5 IKKBZ linearization export path, and the NeuSO runtime bridge can now apply a validated sidecar join order as an experimental left-deep DuckDB join plan.

Updated: 2026-05-08

Key terms: DuckDB, join order optimizer, PlanEnumerator, QueryGraphManager, large join, IKKBZ, JSON exchange

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

R5 先补上第一段内核导出能力：

- 新增 `adl_linearize_join_order`、`adl_linearization_output`、`adl_ikkbz_k` 三个 ADL setting。
- 在 `InitLeafPlans()` 后生成 ADL-OPT linearization metadata，供 JSON export 和 NeuSO runtime request 使用。
- 单独使用 `adl_linearize_join_order` 时仍然只导出，不修改 `PlanEnumerator` 的 `plans`，所以 DuckDB chosen plan 不变。
- 对 cyclic inner join graph 先按 DuckDB estimated selectivity 构造 MST，再导出 IKKBZ-style root candidates。
- R5 实验输入收窄为 regular inner pair graph；复杂 join 留给后续 constraint model / hypergraph export，不在当前 PR 里展开支持。
- 当前 `k-best` 只来自 top-k root result，不做 tie-break perturbation、near-MST 或多 edge weight 策略。
- 面向使用者的参数、查看结果和测试说明见 `docs/design-docs/ikkbz-linearization-export-usage.md`。
- 面向开发者的 DuckDB join-order 源码链路和 R5 插入点说明见 `docs/design-docs/duckdb-join-order-code-path.md`。

## R5 使用入口

R5 的最短 smoke 命令：

```bash
./build/reldebug/duckdb /tmp/adl-opt-r5-smoke.duckdb \
  < scripts/adl_opt/r5_ikkbz_linearization_smoke.sql
```

查看结果：

- `EXPLAIN` 输出里找 `adl_join_linearization`。
- 完整 JSON 默认写到 `/tmp/adl-opt-linearization.json`。
- `status=ok` 表示拿到了 linear order candidates；`skipped_not_large_join` 表示 relation 数小于 12；`unsupported` 表示当前可重排子图没有通过 R5 的 regular inner pair graph guard。

这条路径只能回答“DuckDB 内部能否为这个 large join 导出 IKKBZ-style 线性候选”。它不能证明 DuckDB 已经使用这些 order，也不能作为性能加速结论。

## NeuSO Runtime Applied Path

当同时开启 `adl_linearize_join_order=true` 和 `adl_neuso_runtime_enabled=true` 时，DuckDB 会在 join-order pass 内完成这条实验链路：

```text
InitLeafPlans()
  -> ADLOptJoinLinearizer::Generate()
  -> NeuSORuntimeBridge::InvokeIfEnabled()
  -> PlanEnumerator::ApplyJoinOrder(response.join_order)
  -> QueryGraphManager::Reconstruct()
```

也就是说，sidecar 返回的 `join_order` 通过校验后会被构造成 left-deep `DPJoinNode` 链并写入 `PlanEnumerator::plans`。这一条路径会改变 DuckDB chosen join plan，只应该作为 ADL-OPT 实验 setting 使用。未开启 NeuSO runtime、relation 数小于阈值、或没有可用 sidecar order 时，DuckDB 仍回到原生 `SolveJoinOrder()`。

## 相关 DuckDB 文件

- `src/optimizer/optimizer.cpp`：内置 optimizer pass 编排。
- `src/optimizer/join_order/join_order_optimizer.cpp`：join-order pass 入口。
- `src/optimizer/join_order/query_graph_manager.cpp`：抽取可重排 relation 和 join edge。
- `src/optimizer/join_order/plan_enumerator.cpp`：枚举和求解 join order。
- `src/optimizer/join_order/cost_model.cpp`：计划代价模型。
- `src/optimizer/join_order/cardinality_estimator.cpp`：基数估计。
- `src/optimizer/join_order/adl_opt_join_linearizer.cpp`：R5 IKKBZ/MST linearization metadata。
- `src/optimizer/join_order/neuso_runtime_bridge.cpp`：NeuSO sidecar request/response 和 response order 校验。
- `src/include/duckdb/optimizer/optimizer_extension.hpp`：pre/post optimizer extension API。

## 为什么 v0 不直接用 Optimizer Extension

DuckDB optimizer extension 可以在内置 optimizer 之前或之后修改 logical plan。它适合：

- 收集/标注 logical plan。
- 做额外 rewrite。
- 验证外部 pass 的可加载性。

但 ADL-OPT 未来如果要真正替代 join-order decision，最自然的位置在 join-order enumerator 内部。仅靠 extension 难以直接替换 `PlanEnumerator` 的搜索策略。

## 未来 in-tree 方向

v0 之后可以规划一个 experimental large-join bridge：

- 从 `QueryGraphManager` 读取 relation ids、join edges、filters、estimated cardinality 和 cost feature。
- 将 large-join graph 导出为 JSON。
- 从外部 ADL-OPT JSON 读取 linear order 和 endpoint append path。
- 只在 `n > 12` approximate path 中尝试使用 ADL-OPT path。
- 若 JSON 缺失、无效或 relation count 不满足阈值，回退 DuckDB 当前 enumerator。
- 用 setting 或 build flag 控制启用。

这个方向已经在实验 setting 下跑通第一版：读取 NeuSO runtime decision，并通过 `PlanEnumerator::ApplyJoinOrder()` 应用为 left-deep plan。后续重点是 fallback 策略、scope metadata、模型质量和 benchmark。

## JSON 交换边界

第一版不新增 DuckDB public API。推荐用调试 setting 或本地实验 build flag 指定：

- export path：DuckDB 写出 query graph JSON。
- decision path：DuckDB 读取 ADL-OPT 输出的 order/path JSON。
- mode：`off`、`export_only`、`apply_if_valid`。

JSON 失败必须 fail closed：记录错误，回退 DuckDB 当前策略，而不是生成不完整计划。

## JSON Writer 选择

R5 内核导出使用 DuckDB 已经 vendored 的 `yyjson`，而不是手写字符串或引入新的 JSON 依赖。

这个选择主要来自三点：

- DuckDB 代码库里已经有多处 `yyjson` 使用，例如 profiling、variant/json 转换和 JSON plan/tree rendering。
- ADL-OPT 导出对象包含嵌套数组、浮点数、字符串和错误信息，用结构化 JSON API 可以避免手写转义和拼接错误。
- R5 是 optimizer 内部 export-only 路径，复用现有低层 JSON writer 比引入额外 C++ JSON abstraction 更小、更贴近 DuckDB 当前实现风格。

如果后续 DuckDB 上游提供更统一的内部 JSON builder，可以再把 `ADLOptJoinLinearizer` 的序列化部分迁过去；当前 PR 先保持依赖面最小。

## v0 Plan-Control 注意点

固定 join order 的 SQL variant 必须满足：

- join graph connected。
- 每一步只追加一个相邻 relation。
- SQL 结果与 default 一致。
- `EXPLAIN` 显示的 join tree 与预期 path 一致，或标记为 uncontrolled。

如果 DuckDB 仍然重排 join tree，该 variant 不能用于 speedup 结论。
