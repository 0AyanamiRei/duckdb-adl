# NeuSO Runtime DuckDB Bridge

English TL;DR: DuckDB now pre-starts an experimental NeuSO Python sidecar, generates IKKBZ-style base linear orders inside join-order optimization, sends the join graph plus base order to NeuSO over HTTP JSON, validates the returned relation-id order, and still leaves DuckDB's native plan unchanged for this phase.

Updated: 2026-05-09

Key terms: NeuSO, DuckDB, runtime inference, join order, IKKBZ, sidecar, relation id, left-deep plan

## 目标边界

本文只说明 DuckDB 在线优化阶段如何接入 NeuSO。它不讨论模型训练、离线数据集生成、标签采样、benchmark 设计，也不把 NeuSO 返回的 order 绑定到最终 physical rule。

当前实现的目标是验证运行时接口闭环：

- DuckDB 能自动管理 Python sidecar。
- DuckDB 能在 join-order optimization 中构造 NeuSO runtime request。
- Request 包含 DuckDB join graph、optimizer-stage estimated feature，以及 PR5 线性化路径产生的 base linear order。
- NeuSO sidecar 能返回合法 relation-id `join_order`。
- DuckDB 能校验 response 属于当前 request，并校验 order 是合法 permutation 和 connected append path。

当前实现仍然不改变 DuckDB 的最终计划。`NeuSORuntimeBridge::InvokeIfEnabled()` 校验 response 后返回，随后 `PlanEnumerator::SolveJoinOrder()` 继续走 DuckDB 原生逻辑。后续真正应用 NeuSO order 时，才需要把 response 转成 forced left-deep `DPJoinNode` 链并写回 `plans` 表。

第一版支持范围仍然很窄：

- 只在 `relation_count >= PlanEnumerator::THRESHOLD_TO_SWAP_TO_APPROXIMATE` 时调用 NeuSO runtime bridge。
- 只支持 regular inner pair graph。
- 不支持 outer、semi、anti、ASOF、MARK、SINGLE、dependent/delim join。
- 不支持 hyper-edge 或需要复杂 legality constraint 的 join 子图。
- 不新增 DuckDB public API。

## 当前链路

当前代码路径是：

```text
DuckDB CLI -cmd / DBConfig
  -> 设置 adl_neuso_sidecar_command/host/port/timeout
  -> 可选设置 adl_linearize_join_order / adl_ikkbz_k
  -> SET adl_neuso_runtime_enabled = true
     -> AdlNeusoRuntimeEnabledSetting::OnSet()
     -> NeuSORuntimeBridge::EnsureStarted()
     -> fork Python sidecar 并等待 /health

workload SQL
  -> parser / binder / planner
  -> JoinOrderOptimizer::Optimize()
  -> QueryGraphManager::Build()
  -> CostModel
  -> PlanEnumerator::InitLeafPlans()
  -> ExportADLOptJoinLinearization()
     -> 如果 adl_linearize_join_order=true:
        ADLOptJoinLinearizer::Generate()
        生成 ADLOptJoinLinearizationResult::linear_orders
        可选写 adl_linearization_output
  -> NeuSORuntimeBridge::InvokeIfEnabled(query_graph_manager, cost_model, linear_orders)
     -> relation_count 小于 large-join threshold 时跳过
     -> sidecar 已预启动且配置匹配时不再做 /health
     -> BuildRequestJSON()
     -> POST /infer_join_order
     -> ValidateResponse()
  -> PlanEnumerator::SolveJoinOrder()
  -> QueryGraphManager::Reconstruct()
  -> execution
```

这个顺序很重要：PR5 的线性化结果先生成，再作为 NeuSO request 中的 `base_linear_order` / `candidate_linear_orders` 传给模型。NeuSO 目前只参与“输入/输出和合法性验证”，不接管 `PlanEnumerator`。

相关代码位置：

- `src/main/settings/custom_settings.cpp`：`AdlNeusoRuntimeEnabledSetting::OnSet()` 触发 sidecar 预启动。
- `src/optimizer/join_order/join_order_optimizer.cpp`：先调用 `ExportADLOptJoinLinearization()`，再调用 `NeuSORuntimeBridge::InvokeIfEnabled()`。
- `src/optimizer/join_order/adl_opt_join_linearizer.cpp`：生成 IKKBZ/MST-style `linear_orders`。
- `src/optimizer/join_order/neuso_runtime_bridge.cpp`：管理 sidecar、构造 request、发送 HTTP JSON、校验 response。
- `scripts/adl_opt/neuso_runtime_sidecar.py`：Python HTTP sidecar。
- `scripts/adl_opt/neuso_runtime_bridge_smoke.py`：文件驱动 regression runner。

## Settings

NeuSO runtime bridge 使用这些 experimental local settings：

- `adl_neuso_runtime_enabled`：启用 runtime bridge，并在 setting 生效时预启动 sidecar。
- `adl_neuso_sidecar_command`：启动 Python sidecar 的 shell command。
- `adl_neuso_sidecar_host`：sidecar host，默认 `127.0.0.1`。
- `adl_neuso_sidecar_port`：sidecar port，默认 `8765`。
- `adl_neuso_sidecar_timeout_ms`：sidecar health check 和 request timeout。

PR5 线性化 settings 与 NeuSO runtime bridge 是相邻但不同的功能：

- `adl_linearize_join_order`：打开 IKKBZ linearization export 和内存中的 `linear_orders` 生成。
- `adl_ikkbz_k`：请求导出的 root candidate 数量。
- `adl_linearization_output`：可选 full JSON 输出文件；NeuSO runtime bridge 不依赖这个文件，而是直接使用内存中的 `ADLOptJoinLinearizationResult::linear_orders`。

当前 regression runner 会通过 DuckDB CLI `-cmd` 同时设置：

```sql
SET adl_neuso_sidecar_command = '...';
SET adl_neuso_sidecar_host = '127.0.0.1';
SET adl_neuso_sidecar_port = ...;
SET adl_neuso_sidecar_timeout_ms = 10000;
SET adl_linearize_join_order = true;
SET adl_ikkbz_k = 1;
SET adl_neuso_runtime_enabled = true;
```

这样 workload SQL 进入 optimizer 前，sidecar 已经启动；进入 optimizer 后，request 会带上 PR5 线性化生成的 base order。

## Sidecar 生命周期

`NeuSOSidecarProcess` 是 DuckDB 进程内的单例管理器。

启动路径：

1. `SET adl_neuso_runtime_enabled = true` 触发 setting callback。
2. 如果 callback 有 `ClientContext`，调用 `NeuSORuntimeBridge::EnsureStarted(ClientContext&)`；否则调用 `EnsureStarted(DBConfig&)`。
3. `EnsureStarted()` 先访问 `/health`。
4. 如果 sidecar 没有响应，POSIX 平台使用 `fork()`、`setsid()` 和 `/bin/sh -c <command>` 启动子进程。
5. 子进程 stdout/stderr 重定向到 `/dev/null`。
6. DuckDB 轮询 `/health`，直到 sidecar ready 或超时。

优化阶段路径：

- `InvokeIfEnabled()` 先读取当前 session settings。
- 如果 bridge 未启用，直接返回。
- 如果 relation 数量低于 large-join threshold，直接返回。
- 如果 sidecar 已经以相同 command/host/port 启动，直接发送 `/infer_join_order`，不再额外做 `/health`。
- 如果 sidecar 没有启动，调用 `EnsureStarted()` 作为兜底。

当前没有实现配置热切换，也没有在 `SET adl_neuso_runtime_enabled=false` 时停止 sidecar。这两个行为暂时不是本阶段目标。

## DuckDB 给 NeuSO 的输入

DuckDB 给 NeuSO 的 request 表达当前 join-order 子问题，而不是 SQL 文本。跨边界主键是 DuckDB join-order relation id；alias/debug label 只用于调试。

当前 request 的字段形态如下。`relations` 和 `edges` 数组只展示代表性条目；真实完整 request 可以通过 regression runner 的 `actual_request.json` 查看。

```json
{
  "version": 1,
  "request_id": "duckdb_neuso_140735612345678",
  "graph_hash": "8f31c2a4b5d6e7f8",
  "mode": "linear_join_order",
  "scope": {
    "relation_count": 12,
    "large_join_threshold": 12,
    "supported_shape": "regular_inner_pair_graph"
  },
  "relations": [
    {
      "relation_id": 0,
      "debug_label": "t0",
      "alias": "t0",
      "table": "t0",
      "base_cardinality": 100,
      "estimated_cardinality": 100,
      "degree": 1
    }
  ],
  "edges": [
    {
      "edge_id": 0,
      "left_relation_id": 0,
      "right_relation_id": 1,
      "join_type": "INNER",
      "predicate_type": "EQUAL",
      "estimated_pair_cardinality": 100,
      "selectivity": 0.01,
      "estimated_join_cost": 100
    }
  ],
  "base_linear_order": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
  "candidate_linear_orders": [
    {
      "linear_order_id": "ikkbz_root_0",
      "relation_id_order": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    }
  ]
}
```

字段说明：

- `version`：当前为 `1`。
- `request_id`：当前用 `QueryGraphManager` 指针地址生成，只用于同一请求/响应匹配。
- `graph_hash`：由 relation id 列表和 regular inner edge 列表生成；当前不包含 cardinality、cost、base order 或 request id。
- `mode`：当前固定为 `linear_join_order`。
- `scope.relation_count`：当前 join-order 子图 relation 数量。
- `scope.large_join_threshold`：DuckDB large-join approximate threshold。
- `scope.supported_shape`：当前固定为 `regular_inner_pair_graph`。
- `relations[*].relation_id`：DuckDB join-order relation id。
- `relations[*].debug_label` / `alias` / `table`：来自 `RelationStats::table_name`，为空时回退为 `r<id>`。
- `relations[*].base_cardinality`：`RelationStats::cardinality`。
- `relations[*].estimated_cardinality`：当前实现与 `base_cardinality` 相同。
- `relations[*].degree`：request 构造时按 join graph edge 统计。
- `edges[*].left_relation_id` / `right_relation_id`：pair edge 两端 relation id。
- `edges[*].join_type`：当前只允许 `INNER`。
- `edges[*].predicate_type`：来自 `ExpressionTypeName(filter->GetExpressionType())`。
- `edges[*].estimated_pair_cardinality`：`CostModel::cardinality_estimator.EstimateCardinalityWithSet(pair_set)`。
- `edges[*].selectivity`：`pair_cardinality / (left_cardinality * right_cardinality)`。
- `edges[*].estimated_join_cost`：当前直接复用 `pair_cardinality`。
- `base_linear_order`：`linear_orders[0]`，来自 `ADLOptJoinLinearizer::Generate()`。
- `candidate_linear_orders[*].relation_id_order`：所有传入 bridge 的 candidate order；当前 regression 用 `adl_ikkbz_k=1`，因此只有一个候选。

`base_linear_order` 是第一版 NeuSO runtime bridge 的硬约束。如果没有打开 `adl_linearize_join_order`，或线性化结果为空，DuckDB 不会发送无 base order 的 request，而是直接报错。当前 regression 会打开该 setting，并要求测试 trace 中必须出现 `base_linear_order` 和非空 `candidate_linear_orders`。

## Cost 和 Cardinality 边界

NeuSO 在线推理可以使用 DuckDB 优化阶段已经拥有或可以便宜估计的信息：

- base cardinality。
- filter 后 estimated cardinality。
- pair join estimated cardinality。
- selectivity。
- predicate type。
- estimated join/transition cost。
- join graph topology。
- PR5 线性化得到的 base linear order / candidate order。

在线推理不应该要求：

- actual runtime。
- 真实中间结果 cardinality。
- oracle best cost。
- sampled best path。
- benchmark latency label。

因此，NeuSO runtime 接入不是“无代价信息”。它使用的是 optimizer-stage estimated feature，而不是执行后才知道的 label。

## NeuSO 返回 DuckDB 的输出

NeuSO 返回完整线性 join order。这个 order 是 relation id permutation，语义是 left-deep single-relation append。

当前 response 形态如下：

```json
{
  "version": 1,
  "request_id": "duckdb_neuso_140735612345678",
  "graph_hash": "8f31c2a4b5d6e7f8",
  "status": "ok",
  "model_version": "neuso_contract_smoke",
  "join_order": [0, 1, 2, 3],
  "latency_ms": 4.2
}
```

C++ bridge 当前校验这些字段：

- `version` 必须为 `1`。
- `status` 必须为 `ok`。
- `model_version` 必须是非空字符串。
- `request_id` 必须与当前 request 完全匹配。
- `graph_hash` 必须与当前 request 完全匹配。
- `join_order` 必须是 unsigned integer 数组。

`latency_ms` 是 sidecar 和 regression runner 使用的观测字段；C++ bridge 当前不要求它存在，也不使用它做决策。runner 会要求 sidecar response 包含 `latency_ms`，用于接口验证和 CPU/GPU smoke 对比。

`join_order` 的语义：

```text
r0
(r0 join r1)
((r0 join r1) join r2)
(((r0 join r1) join r2) join r3)
...
```

NeuSO 不返回 bushy tree，也不返回 pair-merge decision。DuckDB 未来应用它时，应把它解释为 left-deep single-relation append order。

## 校验和失败处理

C++ bridge 不信任 sidecar response。当前校验分三层。

Request 构造前置条件：

- `InvokeIfEnabled()` 只在 `relation_count >= PlanEnumerator::THRESHOLD_TO_SWAP_TO_APPROXIMATE` 时继续。
- `BuildRequestJSON()` 只接受 `JoinType::INNER`。
- `BuildRequestJSON()` 只接受 singleton-pair edge，即 `left_set->count == 1 && right_set->count == 1`。
- `relation_count > 1` 但没有 join edge 时直接报错。

Response 结构校验：

- JSON 必须能被 yyjson 解析。
- root 必须是 object。
- `version == 1`。
- `status == "ok"`。
- `model_version` 非空。
- `request_id` 匹配当前 request。
- `graph_hash` 匹配当前 request。
- `join_order` 是 unsigned integer array。

Join order 语义校验：

- `join_order.size() == relation_count`。
- 每个 relation id 都在 `[0, relation_count)`。
- 每个 relation id 正好出现一次。
- 从第二个 relation 开始，每一步 append 的 relation 都必须与已加入集合中至少一个 relation 有 query graph connection。

如果任一校验失败，或 sidecar 超时、崩溃、返回 non-200/malformed JSON，当前实现直接抛出 `InvalidInputException`。这符合“先验证接口，不处理失败回退”的阶段目标。

未来当 NeuSO order 开始影响最终计划时，应改成 fail-closed fallback：

```text
记录 request_id / graph_hash / error reason
丢弃 NeuSO response
继续使用 DuckDB 原生 PlanEnumerator::SolveJoinOrder()
```

## DuckDB 侧应用语义

当前实现不应用 NeuSO 返回的 `join_order`。`NeuSORuntimeBridge::InvokeIfEnabled()` 校验通过后返回，`JoinOrderOptimizer::Optimize()` 继续调用 `plan_enumerator.SolveJoinOrder()`。

未来应用方向是新增内部 helper，例如：

```cpp
bool TryBuildForcedLeftDeepPlan(
    QueryGraphManager &query_graph_manager,
    CostModel &cost_model,
    PlanEnumerator &plan_enumerator,
    const vector<idx_t> &join_order
);
```

该 helper 应做这些事：

1. 按 `join_order` 找 singleton `JoinRelationSet`。
2. 从第一个 relation 开始构造 left-deep join 链。
3. 对每一步 append，复用 `QueryGraphEdges::GetConnections()` 找 join filter。
4. 为每个中间 set 生成 `DPJoinNode`。
5. 写入 `PlanEnumerator` 的 `plans` 表。
6. 让最终 full relation set 对应 NeuSO forced plan。

随后仍走：

```cpp
query_graph_manager.plans = &plan_enumerator.GetPlans();
QueryGraphManager::Reconstruct();
```

这样可以复用 DuckDB 原生的 join condition、filter 消费、cardinality 写回和 logical plan reconstruction 逻辑。应避免在 `Reconstruct()` 之后再改写 logical plan。

## Sidecar 和 Native Runtime 的共同边界

当前实现使用 Python sidecar，因为它最容易复用 NeuSO PyTorch 代码。

Sidecar endpoint：

- `GET /health`：返回 sidecar health 和 `model_version`。
- `POST /infer_join_order`：接收 runtime request，返回 response。

Sidecar 行为：

- `scripts/adl_opt/neuso_runtime_sidecar.py` 启动 `ThreadingHTTPServer`。
- `NeuSOSidecar.infer()` 内部加锁，因此当前同一 sidecar 进程内推理是串行的。
- 可选 `--trace-file` 会写出最后一次 request/response。
- 当前 smoke 使用 deterministic mock scorer，不证明模型质量或性能收益。

未来如果做 C++ native inference，应保持相同语义边界：

```text
DuckDB optimizer-stage feature + base linear order
  -> NeuSO-compatible request
  -> relation-id linear join_order
  -> DuckDB validation
  -> forced left-deep plan
```

Sidecar 和 native runtime 的差异只应该体现在 transport 和模型执行方式上，不应该改变 DuckDB 对 request/response 的解释。

## 验证方式

当前推荐的测试是文件驱动 regression。测试输入 SQL 和 expected response 放在文件中，runner 负责让 DuckDB 执行 SQL、自动拉起 sidecar、读取 sidecar trace，并比较 normalized response。

测试目录：

```text
scripts/adl_opt/testdata/neuso_runtime_bridge/chain_12/
  input.sql
  expected_response.json
```

文件含义：

- `input.sql`：实际喂给 DuckDB CLI 的 workload SQL。
- `expected_response.json`：稳定标准输出，只比较 `version`、`status`、`model_version`、`join_order`。

`input_request.json` 已删除。当前 request 必须由 DuckDB 真实优化阶段生成，这样测试才能覆盖：

- PR5 `ADLOptJoinLinearizer::Generate()` 是否生成 base order。
- NeuSO request 是否强制携带 `base_linear_order` / `candidate_linear_orders`。
- DuckDB sidecar bridge 是否能完成 HTTP JSON request/response。
- DuckDB C++ response 校验是否通过。

单 case CPU：

```bash
PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py \
  --mode regression \
  --duckdb build/reldebug/duckdb \
  --case-dir scripts/adl_opt/testdata/neuso_runtime_bridge/chain_12 \
  --device cpu
```

单 case CUDA：

```bash
PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py \
  --mode regression \
  --duckdb build/reldebug/duckdb \
  --case-dir scripts/adl_opt/testdata/neuso_runtime_bridge/chain_12 \
  --device cuda
```

扫描全部 regression cases：

```bash
PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py \
  --mode regression \
  --duckdb build/reldebug/duckdb \
  --testdata-dir scripts/adl_opt/testdata/neuso_runtime_bridge \
  --device cpu
```

保留 actual 文件：

```bash
PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py \
  --mode regression \
  --duckdb build/reldebug/duckdb \
  --case-dir scripts/adl_opt/testdata/neuso_runtime_bridge/chain_12 \
  --device cpu \
  --output /tmp/neuso-runtime-regression
```

输出文件：

```text
/tmp/neuso-runtime-regression/chain_12/
  duckdb_runtime_trace.json
  actual_request.json
  actual_response.json
  actual_response.normalized.json
```

重点检查：

```bash
python3 - <<'PY'
import json
with open('/tmp/neuso-runtime-regression/chain_12/duckdb_runtime_trace.json') as f:
    request = json.load(f)['request']
print(request.get('base_linear_order'))
print(request.get('candidate_linear_orders'))
PY
```

应能看到 `base_linear_order` 是完整 relation-id permutation，并且 `candidate_linear_orders` 至少包含一个候选。

`duckdb-runtime` 模式用于快速验证 SQL -> DuckDB -> sidecar -> DuckDB validation：

```bash
PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py \
  --mode duckdb-runtime \
  --duckdb build/reldebug/duckdb \
  --database /tmp/neuso-runtime-smoke.duckdb \
  --output /tmp/neuso-runtime-smoke
```

脚本还保留少量辅助/兼容模式：

- `--mode golden`：`--mode regression` 的历史兼容 alias，不作为推荐叫法。
- `--mode fixture`：使用脚本内置 request 验证 NeuSO adapter 和 response contract，不经过 DuckDB。
- `--mode duckdb-export`：读取 PR5 线性化 export JSON 并适配为 NeuSO request；它不覆盖真实 sidecar round-trip，当前不作为主回归路径。

PR5 线性化 export 仍可单独验证：

```bash
rm -f /tmp/adl-opt-linearization.json
build/reldebug/duckdb < scripts/adl_opt/r5_ikkbz_linearization_smoke.sql
python3 -m json.tool /tmp/adl-opt-linearization.json
```

完整验证建议：

```bash
python3 -m py_compile scripts/adl_opt/neuso_runtime_bridge_smoke.py scripts/adl_opt/neuso_runtime_sidecar.py
cmake --build build/reldebug --target duckdb --config RelWithDebInfo
rm -f /tmp/adl-opt-linearization.json
build/reldebug/duckdb < scripts/adl_opt/r5_ikkbz_linearization_smoke.sql
PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py \
  --mode regression \
  --duckdb build/reldebug/duckdb \
  --case-dir scripts/adl_opt/testdata/neuso_runtime_bridge/chain_12 \
  --device cpu
git diff --check
```

## 常见失败原因

- `ImportError`：没有使用 NeuSO Python 环境，或缺少 PyTorch、PyG、NetworkX。
- `CUDA was requested`：传了 `--device cuda`，但当前 Python 环境中 `torch.cuda.is_available()` 为 false。
- `Request graph_hash does not match relations/edges`：DuckDB request 的 graph hash 与 runner 重新计算不一致。
- `Request is missing required field: base_linear_order`：DuckDB 没有把 PR5 base order 传给 NeuSO，通常是没有打开 `adl_linearize_join_order` 或线性化失败。
- `Request base_linear_order is not a full relation-id permutation`：PR5 线性化结果没有覆盖当前 join graph 全部 relation。
- `Response request_id does not match request`：sidecar 返回了旧 response 或错误 response。
- `Response graph_hash does not match request`：sidecar 返回的 response 不属于当前 join graph。
- `join_order is not a full relation-id permutation`：NeuSO response 丢失、重复或包含未知 relation id。
- `join_order append is disconnected`：返回 order 不能解释为合法 single-relation append path。
- DuckDB setting 报错：当前 binary 不包含 PR5/PR7 experimental settings。

## 与现有文档的关系

`docs/design-docs/ikkbz-linearization-export-usage.md` 说明 PR5/R5 IKKBZ linearization export 的用户视角。

`docs/design-docs/duckdb-join-order-integration.md` 说明 DuckDB join-order optimizer 的整体接入方向。

`docs/design-docs/neuso-adaptation.md` 说明 NeuSO 思想如何迁移到 ADL-OPT。

本文档只说明运行时接口边界：DuckDB 在线优化阶段如何把 join graph 和 base linear order 交给 NeuSO，并如何校验 NeuSO response。
