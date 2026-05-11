# DuckDB Join-Order Code Path Tutorial

English TL;DR: This tutorial explains DuckDB's native join-order optimizer code path, the R5 ADL-OPT IKKBZ linearization export, and the NeuSO runtime applied path that can replace DuckDB's large-join plan under experimental settings.

Updated: 2026-05-08

Key terms: DuckDB, join order, QueryGraphManager, RelationManager, PlanEnumerator, DPhyp, approximate greedy, R5, IKKBZ, NeuSO, applied path

## 目标和阅读方式

这份文档是给继续做 ADL-OPT / DuckDB join-order 开发和 PR review 的人看的源码链路导读。它回答的是：

- DuckDB 原生 join-order optimizer 从哪里进入。
- join graph、relation set、filter binding 和 DP plan table 是怎么流动的。
- R5 IKKBZ linearization export 和 NeuSO runtime applied path 插在什么位置。
- 为什么 R5 的导出结果可能描述的是一个 join-order 子问题，而不是整条 SQL statement。

它不是 `adl_linearize_join_order` 的用户使用文档，也不是一份 ADR。参数、命令和 JSON 字段的使用方式继续看 `docs/design-docs/ikkbz-linearization-export-usage.md`。

## DuckDB 原生 join-order 链路

DuckDB 的 join-order pass 是内置 optimizer pipeline 的一环。入口在 `Optimizer::RunBuiltInOptimizers()`，join-order 在 filter pullup、filter pushdown、outer join simplification 等 rewrite 之后运行：

```C++
Optimizer::RunBuiltInOptimizers()
  -> JoinOrderOptimizer::Optimize()
    -> QueryGraphManager::Build()
      -> RelationManager::ExtractJoinRelations()
      -> RelationManager::ExtractEdges()
      -> QueryGraphManager::CreateHyperGraphEdges()
    -> PlanEnumerator::InitLeafPlans()
    -> ADLOptJoinLinearizer::Generate()       // only when ADL linearization is enabled
    -> NeuSORuntimeBridge::InvokeIfEnabled()  // only when NeuSO runtime is enabled
    -> PlanEnumerator::ApplyJoinOrder()       // if NeuSO returned an order
       or PlanEnumerator::SolveJoinOrder()    // otherwise DuckDB native path
    -> QueryGraphManager::Reconstruct()
```

```C++
void Optimizer::RunBuiltInOptimizers() {
    RunOptimizer(OptimizerType::EXPRESSION_REWRITER, [&]() { rewriter.VisitOperator(*plan); });

    // try to inline CTEs instead of materialization
    RunOptimizer(OptimizerType::CTE_INLINING, [&]() {
        CTEInlining cte_inlining(*this);
        plan = cte_inlining.Optimize(std::move(plan));
    }); 
    
    ...  /* 执行其他rules */
    
    // then we perform the join ordering optimization
    // this also rewrites cross products + filters into joins and performs filter pushdowns
    RunOptimizer(OptimizerType::JOIN_ORDER, [&]() {
        JoinOrderOptimizer optimizer(context);
        plan = optimizer.Optimize(std::move(plan));
    });
    
    ...
}
```

```C++
unique_ptr<LogicalOperator> JoinOrderOptimizer::Optimize(unique_ptr<LogicalOperator> plan,
                                                         optional_ptr<RelationStats> stats) {
    // extract the relations that go into the hyper graph.
    // We optimize the children of any non-reorderable operations we come across.
    bool reorderable = query_graph_manager.Build(*this, *op);

    // get relation_stats here since the reconstruction process will move all relations.
    auto relation_stats = query_graph_manager.relation_manager.GetRelationStats();
    
    if (reorderable) {
        ...
        ExportADLOptJoinLinearization(context, query_graph_manager, cost_model);
    } else {
        new_logical_plan = std::move(plan);
        ...
    }
}
```

这条链路有两个很重要的事实。

第一，join-order pass 接收的是已经经过前序 optimizer rewrite 的 logical plan。比如外连接可能已经被 `OuterJoinSimplification` 简化成 inner join；所以源码里看到的 join 类型不一定和用户原始 SQL 文本完全一致。

第二，join-order pass 不只是“选一个 join order”。它会先把可重排部分抽成 join graph，再用 `PlanEnumerator` 求解，最后通过 `QueryGraphManager::Reconstruct()` 把求解出的 join tree 写回 logical plan。

## QueryGraphManager、RelationManager

```C++
-> QueryGraphManager::Build()
  -> RelationManager::ExtractJoinRelations()
  -> RelationManager::ExtractEdges()
  -> QueryGraphManager::CreateHyperGraphEdges()
```

`QueryGraphManager::Build()` 抽取当前 optimizer 实例能处理的 join-order 子图。

`RelationManager` 负责把 logical plan 里的可重排输入变成 join-order 内部 relation：

- relation id：join-order optimizer 内部使用的 `RelationIndex`，（类似dphyp中的编号{R1, R2, ...}）。
- relation set：一个或多个 relation id 的集合，对应 hyper node。
- relation stats：后续 cardinality estimator 和 cost model 使用的基础统计信息。

---

`RelationManager::ExtractJoinRelations()` 会沿着 logical plan 递归寻找可重排关系。普通 inner comparison join、cross product 等可以进入当前 join graph；不可重排的边界会触发 child optimizer。

遇到这些边界时，DuckDB 不会把整个 operator 当作普通 inner join graph 重排。它会分别优化 child，然后把这个 non-reorderable operator 作为当前层的一个 relation 参与外层处理。这也是 R5 export scope 问题的根源之一。

可以把它理解成“把不能拆开的东西包成一个黑盒”。比如考虑下面这个sql：

```SQL
FROM (a LEFT JOIN b ON a.k = b.k) AS x
JOIN c ON x.k = c.k
JOIN d ON c.k = d.k
```

`a LEFT JOIN b` 不能被普通 inner join reorder 打散，因为 `LEFT JOIN` 会产生 NULL 扩展行，随便移动 b 会改变语义。DuckDB 遇到这种 non-reorderable operator 时，会先递归优化它的 child，然后把整个 (`a LEFT JOIN b`) 当成父层 join graph 里的一个 relation，比如 `r0`。父层可以考虑 `r0 JOIN c JOIN d` 的顺序，但不能把 a 和 b 拆出来和 c/d 混排。

---

`RelationManager::ExtractEdges()` 接着把 join predicates 和 filters 转成 `FilterInfo`。这些信息会被 `QueryGraphManager::CreateHyperGraphEdges()` 转成 query graph edge：

- base filter 通常只影响 cardinality 估计。
- regular pair comparison join 可以形成普通 join edge。
- 更复杂的 join legality 和 hyperedge 信息仍然属于 DuckDB 原生 optimizer 的职责。

R5 不在这里实现一套通用 join legality model。它只消费 regular inner singleton-pair edge；无法投影成这个形态的子图会被 `ADLOptJoinLinearizer` 里的集中 guard 标成 `unsupported`。当前实验从 SQL workload 侧约束输入，后续若要覆盖复杂 join，需要单独设计 constraint model / hypergraph export。

## PlanEnumerator 做什么

```C++
-> PlanEnumerator::InitLeafPlans()
-> PlanEnumerator::SolveJoinOrder()
-> QueryGraphManager::Reconstruct()
```

`PlanEnumerator` 是 DuckDB join-order 求解的核心。它持有一个 `plans` map，用 `JoinRelationSet` 映射到当前找到的最好 `DPJoinNode`。

---

`InitLeafPlans()` 做两件事：
- 为每个单 relation 初始化 leaf plan。
- 初始化 cardinality estimator 的等价列、relation stats 和基础 cardinality 信息。

---

`SolveJoinOrder()` 决定使用哪条求解路径：
- 当 `relation_count < PlanEnumerator::THRESHOLD_TO_SWAP_TO_APPROXIMATE` 时，优先走 exact DPhyp 风格枚举。
- 当 `relation_count >= PlanEnumerator::THRESHOLD_TO_SWAP_TO_APPROXIMATE` 时，进入 approximate greedy pair merge。
- 如果 exact path 因枚举 pair 数过多提前停止，也会 fallback 到 approximate path。

当前阈值是 `12`。这就是 ADL-OPT 后续把 large-join 研究重点放在 `n >= 12` 的直接代码依据。

---

`SolveJoinOrder()` 完成后，`plans` 里应该能找到覆盖当前 join-order 子图全部 relation 的 final plan。随后 `QueryGraphManager::Reconstruct()` 读取 `plans`，递归生成新的 logical join tree，并把尚未消费的 filter push 回去。

---

因此，`plans` 是 DuckDB 最终 chosen join tree 的来源。只要一个功能不修改 `plans`，也不替换 `Reconstruct()` 的输入，它就没有改变 DuckDB 的 join-order decision。

## R5 和 NeuSO Runtime 插入点

R5 的 IKKBZ linearization export 和 NeuSO runtime applied path 都插在 `JoinOrderOptimizer::Optimize()` 的这段位置：

```text
PlanEnumerator::InitLeafPlans()
ADLOptJoinLinearizer::Generate()
NeuSORuntimeBridge::InvokeIfEnabled()
PlanEnumerator::ApplyJoinOrder() or PlanEnumerator::SolveJoinOrder()
QueryGraphManager::Reconstruct()
```

也就是说，ADL-OPT 在 leaf cardinality 和 cardinality estimator 初始化之后读取当前 join-order 子图；如果 runtime 没有返回 order，DuckDB 继续走原生求解；如果 runtime 返回合法 order，则写入 `PlanEnumerator::plans` 后再 reconstruct logical plan。这个位置有几个好处：

- leaf cardinality 和 cardinality estimator 已经初始化。
- R5 可以读取 query graph、filter bindings、relation stats 和估计 cardinality。
- export-only path 不需要修改 `PlanEnumerator::plans`。
- applied path 可以复用 DuckDB 原生 `DPJoinNode` / `Reconstruct()` 机制，而不是在 join-order pass 外部重写 logical plan。

只开启 `adl_linearize_join_order` 时仍然是 export-only：

- `ADLOptJoinLinearizer::Generate()` 读取 `QueryGraphManager` 和 `CostModel`。
- 对 `relation_count >= 12` 的 regular inner comparison graph 构造 estimated selectivity MST。
- 在 MST 上生成 IKKBZ-style root linear order candidates。
- 把 full JSON 写到 `adl_linearization_output` 指定路径。
- 把 compact summary 放到 `ClientData::adl_join_linearization`，供 `EXPLAIN` 输出。

同时开启 `adl_neuso_runtime_enabled` 时会进入 applied path：

- `NeuSORuntimeBridge::InvokeIfEnabled()` 把 graph 和 base order 发给 sidecar。
- C++ bridge 校验 response 的 request id、graph hash、permutation 和 connected append path。
- `PlanEnumerator::ApplyJoinOrder()` 按 response order 构造 left-deep `DPJoinNode` 链。
- `QueryGraphManager::Reconstruct()` 从更新后的 `plans` 生成实际 logical join tree。

R5 不做这些事：

- 不替换 `SolveJoinOrderApproximately()`。
- export-only 模式不修改 `plans`。
- 不在 `PlanEnumerator` 之外手写 logical join tree。
- 不在 join-order pass 外层扫描整条 logical plan 来判断各种特殊 join 类型。

## Settings、ClientData 和 EXPLAIN 数据流

R5 有三个 local setting：

- `adl_linearize_join_order`：是否启用导出。
- `adl_linearization_output`：full JSON 输出路径。
- `adl_ikkbz_k`：请求导出的 top-k root candidate 数量。

当 `adl_linearize_join_order=false` 时，R5 不会写 JSON，也不会向 `EXPLAIN` 增加 ADL-OPT 行。

当导出启用时，数据流是：

```text
JoinOrderOptimizer
  -> ADLOptJoinLinearizer::Generate()
  -> StoreADLOptJoinLinearization()
    -> optional full JSON file
    -> ClientData::adl_join_linearization
  -> PhysicalPlanGenerator::CreatePlan(LogicalExplain)
    -> EXPLAIN row: adl_join_linearization
```

`ClientData` 里的 summary 会在新的 prepared statement 创建时清空，避免上一条查询的 EXPLAIN metadata 泄漏到下一条查询。

如果文件写出失败，R5 应该把错误表达为 `export_error` metadata，而不是让用户查询失败。原因是 R5 是研究导出路径，DuckDB 的执行计划仍由原生 optimizer 负责。

## Export Scope 和递归子问题

R5 的一个容易误解的点是：一次 SQL statement 里可能出现多个 join-order optimizer 调用。

这是 DuckDB 原生逻辑决定的。遇到 non-reorderable operator 时，`RelationManager::ExtractJoinRelations()` 会创建 child optimizer 递归优化子树。于是一个 logical plan 可能长这样：

```text
whole statement
  -> non-reorderable outer wrapper
    -> child: 12-way regular inner join
    -> child: another relation
```

child optimizer 可以看到一个完整的 12-way regular inner join 子图，并导出 `status=ok`。但这个 `ok` 只说明 child join-order subproblem 支持 R5 linearization，不代表整条 SQL statement 没有 outer boundary。

当前 R5 只有一个 `ClientData::adl_join_linearization` summary slot，并用优先级保留更有信息量的导出结果。这样会让内部 large inner join 的 `ok` 出现在最终 `EXPLAIN` 里。这个行为对研究有价值，因为 ADL-OPT 的目标正是 large inner join 子图；但如果外部 consumer 把它误解为 whole-statement export，就会出问题。

因此后续如果让 ADL-OPT runner 消费这个 JSON，应该补充 scope metadata，例如：

- 当前导出是 whole statement 还是 join-order subproblem。
- optimizer recursion depth。
- 当前 subproblem 的 relation count。
- 是否存在 non-reorderable wrapper。

在补这些字段之前，R5 JSON 只能被理解为“某次 join-order optimizer 调用的导出结果”，不能被理解为“整条 SQL 的完整 join graph”。

## Review Checklist

review R5 或后续 ADL-OPT join-order 改动时，优先守住这些点：

- 默认关闭 setting 时，不应该产生 ADL-OPT JSON 或 `EXPLAIN` 行。
- export-only 代码不应该修改 `PlanEnumerator::plans`。
- applied path 只应通过 `PlanEnumerator::ApplyJoinOrder()` 修改 `plans`，并继续让 `QueryGraphManager::Reconstruct()` 生成 logical join tree。
- R5 的支持范围应保持 regular inner pair graph；复杂 join 不应被伪装成完整 regular graph。
- 如果导出的是 child subproblem，文档或后续 schema 必须让 consumer 看得出 scope。
- `EXPLAIN` metadata 必须按 query 清理，不能泄漏到无关 statement。
- 文件写出失败应表现为 structured metadata，不应阻断查询执行。

后续建议补的自动测试包括：

- 默认关闭：同一个 12-way inner join 的 `EXPLAIN` 不出现 `adl_join_linearization`。
- 开启导出：12-way regular inner join 输出 `status=ok`。
- 小查询：`n < 12` 输出 `skipped_not_large_join`。
- 复杂 join：作为 workload 输入约束记录，不作为 R5 smoke 的主要验收对象。
- 递归子问题：后续若外部 consumer 读取 JSON，需要显式表达 export scope。
