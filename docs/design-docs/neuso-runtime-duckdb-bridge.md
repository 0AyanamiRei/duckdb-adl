# NeuSO Runtime DuckDB Bridge

English TL;DR: DuckDB should call NeuSO during join-order optimization with a large regular inner join graph plus optimizer-stage estimates, receive a linear relation-id join order, validate it, and later apply it as a forced left-deep plan.

Updated: 2026-05-09

Key terms: NeuSO, DuckDB, runtime inference, join order, large join, sidecar, relation id, left-deep plan

## 目标边界

这份文档只讨论数据库运行时，也就是一条 SQL 进入 DuckDB 后，在 query optimization 阶段如何接入 NeuSO。它不讨论训练流程、离线数据集生成、JSONL harness、模型标签采样或 benchmark 设计。

这里的 NeuSO 不是直接接收 SQL，也不是在物理执行阶段拦截 operator。DuckDB 仍然负责 parser、binder、logical plan、join graph 抽取、计划重建和执行。NeuSO 只接收 DuckDB 已经抽取好的 join-order 子问题，并返回一个 relation id 线性顺序。

第一版目标仍然限制在 DuckDB large-join approximate 区间：

- 只处理 `n >= 12` 的 large join 子图。
- 只处理 regular inner pair graph。
- 不覆盖 outer、semi、anti、ASOF、MARK、SINGLE、dependent/delim join。
- 不覆盖 hyper-edge 或需要复杂 legality constraint 的 join 子图。
- 不改变 DuckDB public API。

当前实验实现只验证“DuckDB 能否自动管理 Python sidecar 并完成 HTTP JSON request/response 校验”。`adl_neuso_runtime_enabled=true` 会在 setting 生效时预启动 sidecar；后续 join-order optimization 阶段只发送 NeuSO request 并校验 response，避免把 Python 进程冷启动时间混进单条 SQL 的优化耗时。如果模型不可用、超时或返回非法结果，当前实现直接报错，便于开发阶段暴露接口问题。等进入“应用 NeuSO join order 到最终计划”的阶段后，再补充原生 optimizer fallback，避免 NeuSO 降低普通 SQL 的可靠性。

## 运行时链路

推荐链路如下：

```text
SQL
  -> DuckDB CLI -cmd / DBConfig 设置 adl_neuso_* 并预启动 sidecar
  -> workload SQL
  -> parser / binder / planner
  -> logical plan
  -> JoinOrderOptimizer
  -> QueryGraphManager 抽取可重排 join-order 子图
  -> CostModel / CardinalityEstimator 准备估计特征
  -> DuckDB 构造 NeuSO request
  -> 已启动的 NeuSO sidecar 或 native runtime 推理
  -> NeuSO 返回 linear join_order
  -> DuckDB 校验 response
  -> DuckDB 构造 forced left-deep DPJoinNode 链
  -> QueryGraphManager::Reconstruct()
  -> physical plan
  -> execution
```

关键点是：NeuSO 输出不直接替换 SQL，也不直接生成 DuckDB `LogicalOperator`。DuckDB 应该把 NeuSO 返回的 `join_order` 转成 join-order optimizer 能理解的 forced plan，再复用 `QueryGraphManager::Reconstruct()` 生成 logical join tree。sidecar 生命周期由 DuckDB 管理，但启动时机在 workload SQL 进入优化器之前；优化阶段的 `EnsureStarted` 只作为健康检查和兜底。

最自然的 C++ 接入位置在 join-order pass 内部：`QueryGraphManager` 已经完成 relation 和 edge 抽取，`CostModel` 和 cardinality estimator 可以提供优化阶段估计，`PlanEnumerator` 附近可以构造或替换 join-order decision。未来实现时，应避免在 `Reconstruct()` 之后再试图改写已经生成的 logical plan。

## DuckDB 给 NeuSO 的输入

DuckDB 给 NeuSO 的 request 应该表达“当前 join-order 子问题”，而不是原始 SQL 文本。跨边界主键必须是 DuckDB join-order relation id；alias 和 debug label 只用于日志、调试和人工解释。

示例 request：

```json
{
  "version": 1,
  "request_id": "stmt_42_scope_0",
  "graph_hash": "8f31c2...",
  "mode": "linear_join_order",
  "scope": {
    "relation_count": 17,
    "large_join_threshold": 12,
    "supported_shape": "regular_inner_pair_graph"
  },
  "relations": [
    {
      "relation_id": 0,
      "debug_label": "r0",
      "alias": "mi",
      "table": "movie_info",
      "base_cardinality": 14835720,
      "estimated_cardinality": 93210,
      "degree": 3
    }
  ],
  "edges": [
    {
      "edge_id": 0,
      "left_relation_id": 0,
      "right_relation_id": 1,
      "join_type": "INNER",
      "predicate_type": "EQUAL",
      "estimated_pair_cardinality": 12000,
      "selectivity": 0.00017,
      "estimated_join_cost": 105000
    }
  ]
}
```

第一版 request 至少应该包含：

- `relation_id`：跨 DuckDB 和 NeuSO 的稳定主键。
- `relations[*].base_cardinality`：基础 relation cardinality。
- `relations[*].estimated_cardinality`：本地 filter 后的优化阶段估计 cardinality。
- `relations[*].degree`：当前 join graph 中的度，可由 DuckDB 构造 request 时计算。
- `edges[*].left_relation_id` / `right_relation_id`：join graph 边。
- `edges[*].join_type`：第一版应始终是 `INNER`。
- `edges[*].predicate_type`：例如 `EQUAL`、`LESS_THAN`，用于区分 predicate 形态。
- `edges[*].estimated_pair_cardinality`：两端 relation join 后的估计 cardinality。
- `edges[*].selectivity`：由 pair cardinality 和两端 cardinality 推导的估计选择率。
- `edges[*].estimated_join_cost`：如果 DuckDB 侧能便宜计算，应作为可选特征提供。

这些字段是在线推理特征，不是监督标签。它们来自 DuckDB 优化阶段已有的统计、基数估计和代价估计。

## Cost 和 Cardinality 边界

NeuSO 在线推理不应该依赖实际执行后的信息。运行时 request 不应该要求 DuckDB 先执行候选 join，或采样真实中间结果 cardinality。

在线推理可以使用：

- base cardinality。
- filter 后 estimated cardinality。
- pair join estimated cardinality。
- selectivity。
- predicate type。
- DuckDB estimated join cost 或 transition cost。
- join graph topology，例如 degree 和邻接关系。

在线推理不应该要求：

- actual runtime。
- 真实中间结果 cardinality。
- oracle best cost。
- sampled best path。
- benchmark latency label。

换句话说，NeuSO 运行时接入并不是“没有代价信息”。它使用的是 DuckDB 优化阶段能拿到的估计型 cost/cardinality/statistics feature，而不是执行后才知道的真实 label。

## NeuSO 返回 DuckDB 的输出

NeuSO 第一版返回完整线性 join order。这个 order 是 relation id permutation，语义是 left-deep single-relation append。

示例 response：

```json
{
  "version": 1,
  "request_id": "stmt_42_scope_0",
  "graph_hash": "8f31c2...",
  "status": "ok",
  "model_version": "neuso_sidecar_v1",
  "join_order": [3, 8, 1, 0, 2, 5, 4],
  "score": 12.37,
  "latency_ms": 6.4
}
```

`join_order` 的含义是：

```text
r3
(r3 join r8)
((r3 join r8) join r1)
(((r3 join r8) join r1) join r0)
...
```

NeuSO 不返回 bushy tree，也不返回 `(a,b) + (c,d)` 这种 pair-merge decision。它返回的是单 relation 追加序列。DuckDB 可以把它解释为 left-deep join tree。

如果 NeuSO 无法处理当前 request，应返回结构化失败，例如：

```json
{
  "version": 1,
  "request_id": "stmt_42_scope_0",
  "graph_hash": "8f31c2...",
  "status": "unsupported",
  "failure_reason": "non_regular_inner_pair_graph"
}
```

当前实验实现收到非 `ok` status 后会直接报错。未来当 NeuSO order 开始影响最终计划时，应该再改成结构化回退原生 optimizer。

## 校验和失败处理

DuckDB 不能直接信任 NeuSO response。应用前必须校验：

- `request_id` 匹配当前 optimizer 子问题。
- `graph_hash` 匹配当前 request，避免使用旧 response。
- `status == "ok"`。
- `join_order` 长度等于 `relation_count`。
- 每个 relation id 正好出现一次。
- 每个 relation id 都在当前 `QueryGraphManager` 子图中存在。
- 当前子图仍满足 `n >= 12` 和 regular inner pair graph 约束。
- 每一步 append 的 relation 与当前 joined set 至少存在一条 join edge，除非显式允许 cross product。

如果任一校验失败，或 sidecar 超时、崩溃、返回 malformed JSON，当前实验实现直接报错：

```text
记录调试 metadata
丢弃 NeuSO response
抛出 InvalidInputException / NotImplementedException
```

这符合当前“先验证接口，不处理失败回退”的阶段目标。未来如果启用 NeuSO 来实际改变 plan，这里应改为 fail closed fallback：记录调试 metadata、丢弃 NeuSO response、继续使用 DuckDB 原生 `PlanEnumerator` / approximate greedy path。

## DuckDB 侧应用语义

DuckDB 收到合法 `join_order` 后，还需要把它应用到 join-order optimizer 内部结构。仅仅拿到数组不会改变最终计划。

推荐实现方向是新增一个内部 helper，例如：

```text
BuildForcedLinearPlan(join_order)
```

它负责：

- 按 `join_order` 找到对应 singleton `JoinRelationSet`。
- 从第一个 relation 开始构造 left-deep join 链。
- 每一步选择当前 joined set 和新增 relation 之间的可用 `NeighborInfo` / join filter。
- 调用或复用 `CreateJoinTree` 风格逻辑生成 `DPJoinNode`。
- 将每个中间 set 的 forced node 写入 `PlanEnumerator` 的 `plans` 表。
- 最终让 full relation set 对应 NeuSO forced plan。

随后继续走：

```text
query_graph_manager.plans = &plan_enumerator.GetPlans()
QueryGraphManager::Reconstruct()
```

这样做的好处是，join condition 反转、filter 消费、estimated cardinality 写回、logical join tree 重建等逻辑仍然由 DuckDB 原生路径处理。NeuSO 只负责 order decision。

## Sidecar 和 Native Runtime 的共同边界

第一版可以先用 Python sidecar，因为它最容易复用现有 NeuSO PyTorch 代码。未来如果要做 C++ native inference，也应该复用同一个语义边界：

```text
DuckDB runtime features
  -> NeuSO-compatible inference input
  -> relation-id linear join_order
  -> DuckDB validation
  -> forced left-deep plan
```

sidecar 和 native runtime 的差别只应该体现在 transport 和模型执行方式上，不应该改变 DuckDB 对 response 的解释方式。

因此，内部 wire contract 应保持：

- request 使用 relation id 表达 join graph。
- request 使用 optimizer-stage estimated feature。
- response 使用 relation id permutation。
- DuckDB 独立负责校验；fallback 在后续真正应用 NeuSO order 时补齐。

## 验证方式

NeuSO runtime bridge 的第一版验证目标不是证明模型能带来性能提升，而是证明接口契约成立。测试人员需要能直观看到“这条 SQL 对应什么 join graph request，以及 NeuSO 应该返回什么稳定格式”。因此，推荐使用文件驱动 regression case，而不是只看脚本打印的临时 response。

当前稳定回归测试入口是：

```text
scripts/adl_opt/neuso_runtime_bridge_smoke.py --mode regression
```

### Regression Case 结构

测试数据放在：

```text
scripts/adl_opt/testdata/neuso_runtime_bridge/
```

每个 case 是一个目录。例如初始 case：

```text
scripts/adl_opt/testdata/neuso_runtime_bridge/chain_12/
  input.sql
  expected_response.json
```

两个核心文件的含义是：

- `input.sql`：实际喂给 DuckDB CLI 的 workload SQL。runner 会先通过 DuckDB CLI `-cmd` 设置 `adl_neuso_sidecar_*`，再设置 `adl_neuso_runtime_enabled=true` 触发 sidecar 预启动；`input.sql` 执行到 join-order optimization 阶段时只负责发送运行时 request 给 NeuSO。
- `expected_response.json`：从 DuckDB 侧 sidecar trace 中取出的 response 标准答案，只包含稳定字段，例如 `status`、`model_version`、`join_order`。`request_id`、`graph_hash`、`latency_ms` 这类动态字段不参与 regression 精确比较。

初始 `chain_12` case 表示 12 张表按 `t0.i = t1.i = ... = t11.i` 形成的 regular inner chain join。DuckDB 实际 runtime request 会写入输出目录中的 `duckdb_runtime_trace.json`，其中包含 relation、edge、cardinality、selectivity 和 cost feature。期望 response 是：

```json
{
  "status": "ok",
  "model_version": "neuso_contract_smoke",
  "join_order": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
}
```

实际 response trace 中仍保留 `request_id` 和 `graph_hash`，runner 会先校验它们与 request 匹配；只是 normalized response 与 `expected_response.json` 对比时不固定这些动态值。

### File-Driven Regression 命令

单 case 回归：

```bash
PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py \
  --mode regression \
  --duckdb build/reldebug/duckdb \
  --case-dir scripts/adl_opt/testdata/neuso_runtime_bridge/chain_12 \
  --device cpu
```

扫描全部 regression cases：

```bash
PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py \
  --mode regression \
  --duckdb build/reldebug/duckdb \
  --testdata-dir scripts/adl_opt/testdata/neuso_runtime_bridge \
  --device cpu
```

如果需要保留 actual 文件用于人工检查：

```bash
PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py \
  --mode regression \
  --duckdb build/reldebug/duckdb \
  --case-dir scripts/adl_opt/testdata/neuso_runtime_bridge/chain_12 \
  --device cpu \
  --output /tmp/neuso-runtime-regression
```

脚本会在 `/tmp/neuso-runtime-regression/chain_12/` 下写出：

```text
duckdb_runtime_trace.json
actual_request.json
actual_response.json
actual_response.normalized.json
```

`duckdb_runtime_trace.json` 是 sidecar 看到的真实 request/response；`actual_response.json` 保留 `request_id`、`graph_hash` 和 `latency_ms`，`actual_response.normalized.json` 是去掉动态字段后参与 regression 对比的内容。如果 actual 和 expected 不一致，runner 会打印 unified diff 并以非零状态退出。

如果本机 NeuSO Python 环境支持 CUDA，可以额外运行：

```bash
PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py \
  --mode regression \
  --duckdb build/reldebug/duckdb \
  --case-dir scripts/adl_opt/testdata/neuso_runtime_bridge/chain_12 \
  --device cuda
```

CUDA 路径只用于确认 NeuSO runtime 可以在 GPU device 上完成相同接口契约，不把 GPU latency 写入 regression oracle。

### Runner 校验内容

regression runner 会校验：

- request 包含 `relation_id`、`relation_count`、`graph_hash`、`relations` 和 `edges`。
- `relation_id` 唯一。
- `graph_hash` 与 `relations` / `edges` 重新计算结果一致。
- 所有 edge 都引用已知 relation。
- NeuSO adapter 能从 relation id 和 edge 构造 query graph。
- actual response 包含 `version`、`request_id`、`graph_hash`、`status`、`model_version`、`join_order` 和 `latency_ms`。
- normalized response 去掉 `request_id`、`graph_hash`、`latency_ms` 后与 `expected_response.json` 精确一致。
- `join_order` 是完整 relation-id permutation。
- `join_order` 的每一步 single-relation append 都与当前 joined set 连通。

这个测试使用 deterministic mock scorer，所以稳定 oracle 是接口格式和合法 `join_order`，不是 learned model 的性能收益。

`--mode golden` 作为历史兼容 alias 暂时保留，内部走同一套 regression runner；新文档和新命令不再推荐使用这个名字。

### Fixture 和 DuckDB Export 模式

`fixture` 模式仍然保留，用于快速检查内置 request 是否能跑通：

```bash
PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py \
  --mode fixture \
  --device cpu
```

但开发人员检查某个 SQL 场景时，应优先新增 regression case，因为 regression case 的输入和期望输出都在文件中，可 review、可 diff、可扩展。

`duckdb-runtime` 模式用于验证当前阶段的单向 runtime bridge。runner 先用 DuckDB CLI `-cmd` 写入 sidecar command/host/port/timeout，再设置 `adl_neuso_runtime_enabled=true`，由 setting callback 预启动 Python sidecar。之后它把 workload SQL 输入 DuckDB CLI；DuckDB 在 join-order optimization 阶段发送 NeuSO runtime request，接收并校验 response，然后继续使用 DuckDB 原生 join-order plan 执行查询。这个模式验证的是：

```text
DuckDB startup/config -> auto-managed sidecar pre-start -> SQL -> DuckDB optimizer -> NeuSO response -> DuckDB validation
```

它暂时不把 NeuSO 返回的 `join_order` 应用到最终计划。

runner 会在 `--output` 目录写出 `duckdb_runtime_trace.json`，其中包含 DuckDB 实际发送给 sidecar 的 request 和 sidecar 实际返回的 response。测试人员可以用这个文件确认 SQL 进入 DuckDB 后跨边界 JSON 的真实形态。

命令示例：

```bash
PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py \
  --mode duckdb-runtime \
  --duckdb build/reldebug/duckdb \
  --database /tmp/neuso-runtime-smoke.duckdb \
  --output /tmp/neuso-runtime-smoke
```

这个模式要求 DuckDB binary 包含 experimental setting：

- `adl_neuso_runtime_enabled`
- `adl_neuso_sidecar_command`
- `adl_neuso_sidecar_host`
- `adl_neuso_sidecar_port`
- `adl_neuso_sidecar_timeout_ms`

`duckdb-export` 模式作为未来 SQL -> DuckDB export -> NeuSO request 的验证路径保留。它会启动 DuckDB CLI，创建 12 张小表，打开 R5-style ADL-OPT linearization export setting，执行一个 12-way regular inner join 的 `EXPLAIN`，读取 DuckDB 导出的 join-order JSON，再交给 NeuSO adapter。

命令示例：

```bash
PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py \
  --mode duckdb-export \
  --duckdb build/reldebug/duckdb \
  --database /tmp/neuso-runtime-smoke.duckdb \
  --output /tmp/neuso-runtime-smoke \
  --device cpu
```

这个模式要求 DuckDB binary 支持以下 experimental setting：

- `adl_linearize_join_order`
- `adl_linearization_output`
- `adl_ikkbz_k`

如果当前分支或 binary 还没有 R5 export 能力，这个模式会失败并提示使用包含这些 setting 的 DuckDB binary。当前 `fixture` 模式仍然可以用于验证 NeuSO 侧 runtime contract。

### 常见失败原因

- `ImportError`：没有使用 NeuSO Python 环境，或缺少 PyTorch、PyG、NetworkX。
- `CUDA was requested`：传了 `--device cuda`，但当前 Python 环境中 `torch.cuda.is_available()` 为 false。
- Regression response mismatch：NeuSO adapter 输出格式或 order 与 `expected_response.json` 不一致，脚本会打印 unified diff。
- `Request graph_hash does not match relations/edges`：fixture 中的 graph hash 没有随 relation/edge 修改同步更新。
- DuckDB setting 报错：当前 DuckDB binary 不包含 R5 export setting。
- DuckDB 没有写出 JSON：`adl_linearization_output` 没生效，或当前 join 子图不满足导出条件。
- `join_order is not a full relation-id permutation`：NeuSO response 丢失、重复或包含未知 relation id。
- `join_order append is disconnected`：返回 order 不能解释为合法 single-relation append path。

## 与现有文档的关系

`neuso-adaptation.md` 说明 NeuSO 思想如何迁移到 ADL-OPT。本文档只说明运行时接口边界。

`duckdb-join-order-integration.md` 说明 DuckDB join-order optimizer 的整体接入方向。本文档把其中“模型在线推理如何与内核交换信息”单独展开。

`feature-and-label-schema.md` 面向离线 JSONL artifact。本文档的 request/response 是运行时实验 wire contract，不等同于离线 artifact schema。
