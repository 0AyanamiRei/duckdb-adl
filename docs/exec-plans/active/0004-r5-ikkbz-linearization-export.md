# 0004 R5 IKKBZ Linearization Export

English TL;DR: Add an export-only DuckDB join-order path that emits IKKBZ-style linear orders for large joins without changing the chosen plan.

Updated: 2026-05-08

Key terms: IKKBZ, AdaptiveQO, selectivity MST, large join, export-only, DuckDB optimizer

## Goal

R5 的目标是在 DuckDB 内核 join-order pass 里拿到真实估计信息，导出给 ADL-OPT 后续使用的线性 join order 候选。

边界要很硬：

- DuckDB `n >= 12` 仍然使用当前 approximate greedy pair merge。
- ADL-OPT linearization 只导出 JSON 和 EXPLAIN 摘要，不改 `PlanEnumerator` 的 `plans`。
- `n < 12` 只标记 `skipped_not_large_join`，不影响 exact DPhyp。
- cyclic query graph 先用 estimated selectivity 做 MST，再在 MST 上产生 IKKBZ-style root candidates。
- `adl_ikkbz_k` 只保留 top-k root results；不做 near-MST、扰动或多套 edge weight 策略。

## Non-Goals

- 不读取外部 ADL-OPT 模型输出。
- 不把 ADL-OPT order 应用回 DuckDB join plan。
- 不把 outer/mark/single/asof、hyperedge 等复杂 join 做成当前实验输入。
- 不承诺 DuckDB 原生 cost model 满足 ASI；这里只用 DuckDB cardinality/selectivity 构造 Cout-compatible ranking metadata。

## Settings

| Setting | Type | Default | 含义 |
| --- | --- | --- | --- |
| `adl_linearize_join_order` | `BOOLEAN` | `false` | 打开导出逻辑。关闭时不写 JSON，也不在 `EXPLAIN` 中增加 ADL-OPT summary。 |
| `adl_linearization_output` | `VARCHAR` | `''` | 完整 JSON 输出路径。为空时只保留 `EXPLAIN` summary。 |
| `adl_ikkbz_k` | `UBIGINT` | `1` | 导出 top-k root candidates。`0` 按 `1` 处理，实际输出为 `min(k, relation_count)`。 |

完整使用说明见 `docs/design-docs/ikkbz-linearization-export-usage.md`。

## Usage

最短 smoke：

```bash
./build/reldebug/duckdb /tmp/adl-opt-r5-smoke.duckdb \
  < scripts/adl_opt/r5_ikkbz_linearization_smoke.sql
```

查看方式：

- `EXPLAIN` 输出中找 `adl_join_linearization`。
- 完整 JSON 默认写到 `/tmp/adl-opt-linearization.json`。
- `linear_orders[*].relation_id_order` 是机器可消费的线性 relation id 顺序。
- `linear_orders[*].relation_label_order` 是给人看的 internal label 顺序，不是 SQL alias。

## Implementation Notes

接入点在 `JoinOrderOptimizer::Optimize()`：

1. `PlanEnumerator::InitLeafPlans()` 初始化 DuckDB cardinality estimator。
2. `PlanEnumerator::SolveJoinOrder()` 正常求解 DuckDB plan。
3. `ADLOptJoinLinearizer::Generate()` 只读取 query graph、relation stats、cardinality estimator 并生成 metadata。
4. `QueryGraphManager::Reconstruct()` 继续使用 DuckDB 原本的 `plans`。

导出的 graph 限制为 regular inner pair comparison join。base filter 会被跳过；当前可重排子图如果不能投影成这个普通 graph，linearizer 内部 guard 会返回 `unsupported`。R5 不在 join-order pass 外层为每种复杂 join 单独写检查；实验 SQL 从 workload 侧约束。

## Validation

构建必须限制 CPU：

```bash
CPU_COUNT=$(nproc 2>/dev/null || sysctl -n hw.ncpu)
BUILD_JOBS=$(( CPU_COUNT * 75 / 100 ))
[ "$BUILD_JOBS" -lt 1 ] && BUILD_JOBS=1
CMAKE_BUILD_PARALLEL_LEVEL=$BUILD_JOBS make reldebug
```

SQL smoke：

- 默认 setting 下 `EXPLAIN` 不出现 `adl_join_linearization`。
- 开启 setting 后，12 表 inner join 的 `EXPLAIN` 出现 `adl_join_linearization`。
- `adl_ikkbz_k=3` 时 JSON 导出 3 个 `linear_orders`。
- cyclic graph 的 MST edge 数量是 `relation_count - 1`。
- `n < 12` 输出 `skipped_not_large_join`。
- 复杂 join 不作为 R5 smoke 的主要验收对象，后续若要覆盖需要单独设计 constraint model / hypergraph export。

## Follow-Up

下一轮才讨论是否读取 ADL-OPT 输出并替换 `n >= 12` approximate path。R5 只是把 DuckDB 里的真实 large-join graph 和线性候选拿出来，让外部 ADL-OPT 有可信输入。
