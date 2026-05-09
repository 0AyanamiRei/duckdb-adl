# R5 IKKBZ Linearization Export Usage

English TL;DR: R5 can export IKKBZ-style linear join-order candidates for DuckDB large joins, but it is debug/export-only and never changes the chosen DuckDB plan.

Updated: 2026-05-08

Key terms: IKKBZ, large join, linearization, ADL setting, EXPLAIN, JSON export, selectivity MST

## 当前支持程度

R5 现在已经能做的是：

- 在 DuckDB join-order optimizer 内部读取真实的 relation count、join filter、relation cardinality 和 cardinality estimator。
- 对 `relation_count >= 12` 的 large join 尝试导出线性化结果。这个阈值和 DuckDB 当前从 exact DPhyp 切到 approximate path 的阈值一致。
- 对 regular inner comparison join graph 构造 estimated selectivity MST，再在 MST 上生成 IKKBZ-style root linear order candidates。
- 通过 `EXPLAIN` 增加一行紧凑 summary，并且可选写完整 JSON 文件。
- 通过 `adl_ikkbz_k` 导出 top-k root candidates。

R5 现在还没有做的是：

- 不把 IKKBZ order 应用回 DuckDB plan；DuckDB chosen plan 仍然来自原来的 join-order optimizer。
- 不读取外部 ADL-OPT 模型或 endpoint-append decision。
- 不做 near-MST、tie-break perturbation 或多套 edge weight/rank 策略。
- 不把 `r0`、`r1` 这类 internal relation label 还原成 SQL alias。当前 JSON 里的 label 是 DuckDB join-order 内部 relation id 的稳定调试名，不是用户写的表别名。

这轮实验把 SQL 输入约束得很窄：目标 workload 应该是 large regular inner join，并且 join predicate 能拆成单表对单表的 comparison edge。DuckDB 原生 optimizer 当然能处理更多 SQL 形态；R5 只是把其中一类适合 IKKBZ/MST 的子问题导出给 ADL-OPT。outer/semi/anti/ASOF/ANY/MARK/SINGLE、hyper-edge、复杂 correlated subquery 等都不作为当前实验输入，后续如果要支持，需要单独做 constraint model / hypergraph export，而不是在这个 linearizer 里继续补分支。

## 参数说明

这些参数都是 ADL setting，默认关闭，不属于 DuckDB public API。

| Setting | Type | Default | 作用 |
| --- | --- | --- | --- |
| `adl_linearize_join_order` | `BOOLEAN` | `false` | 打开 ADL-OPT IKKBZ 线性化导出逻辑。关闭时不会产生 EXPLAIN summary，也不会写 JSON。 |
| `adl_linearization_output` | `VARCHAR` | `''` | 完整 JSON 输出路径。为空时只在 `EXPLAIN` 中显示 summary，不写文件。 |
| `adl_ikkbz_k` | `UBIGINT` | `1` | 请求导出的 root candidate 数量。`0` 会按 `1` 处理；实际输出数量是 `min(k, relation_count)`。 |

这三个 setting 是 local scope，建议在同一个 DuckDB session 中先 `SET`，再执行目标 `EXPLAIN` 或查询。`adl_linearization_output` 每次写一个完整 JSON 对象，不是 JSONL 追加日志；多次执行同一路径时应把它理解为“当前语句的最新导出结果”。

## 快速使用

先用约 75% CPU 构建：

```bash
CPU_COUNT=$(nproc 2>/dev/null || sysctl -n hw.ncpu)
BUILD_JOBS=$(( CPU_COUNT * 75 / 100 ))
[ "$BUILD_JOBS" -lt 1 ] && BUILD_JOBS=1
CMAKE_BUILD_PARALLEL_LEVEL=$BUILD_JOBS make reldebug
```

运行仓库里的 smoke SQL：

```bash
./build/reldebug/duckdb /tmp/adl-opt-r5-smoke.duckdb \
  < scripts/adl_opt/r5_ikkbz_linearization_smoke.sql
```

这个脚本会创建 12 张小表，打开 R5 ADL settings，执行一个 12-way inner join 的 `EXPLAIN`，并把完整结果写到：

```text
/tmp/adl-opt-linearization.json
```

查看 EXPLAIN 里的 summary 时，重点找这一行：

```text
adl_join_linearization
```

典型内容长这样：

```json
{"status":"ok","relation_count":12,"k_emitted":3,"selected_order_id":"ikkbz_root_0"}
```

查看完整 JSON：

```bash
python3 -m json.tool /tmp/adl-opt-linearization.json | less
```

如果装了 `jq`，可以直接看后续 ADL-OPT runner 最关心的候选列表：

```bash
jq '[.linear_orders[] | {linear_order_id, order}]' /tmp/adl-opt-linearization.json
```

## 实际测试例子

这里用仓库里的 `scripts/adl_opt/r5_ikkbz_linearization_smoke.sql` 做例子。这个测试故意很小：每张表只有 100 行，重点不是测性能，而是确认 large-join 线性化导出链路真的跑起来。

测试 SQL 做了三件事：

1. 创建 `t0` 到 `t11` 共 12 张表。
2. 打开 R5 三个 ADL setting，并请求 `k=3`。
3. 对一个 12-way chain inner join 执行 `EXPLAIN`。

核心查询如下：

```sql
SET adl_linearize_join_order = true;
SET adl_linearization_output = '/tmp/adl-opt-linearization.json';
SET adl_ikkbz_k = 3;

EXPLAIN SELECT count(*)
FROM t0
JOIN t1 ON t0.i = t1.i
JOIN t2 ON t1.i = t2.i
JOIN t3 ON t2.i = t3.i
JOIN t4 ON t3.i = t4.i
JOIN t5 ON t4.i = t5.i
JOIN t6 ON t5.i = t6.i
JOIN t7 ON t6.i = t7.i
JOIN t8 ON t7.i = t8.i
JOIN t9 ON t8.i = t9.i
JOIN t10 ON t9.i = t10.i
JOIN t11 ON t10.i = t11.i;
```

运行命令：

```bash
rm -f /tmp/adl-opt-linearization.json
./build/reldebug/duckdb /tmp/adl-opt-r5-smoke-doc-example.duckdb \
  < scripts/adl_opt/r5_ikkbz_linearization_smoke.sql
```

在 `EXPLAIN` 输出最后可以看到 ADL-OPT summary 行。实际输出内容类似：

```json
{"status":"ok","relation_count":12,"k_emitted":3,"selected_order_id":"ikkbz_root_0"}
```

这表示：

- `status=ok`：当前 join graph 在 R5 支持范围内，线性候选导出成功。
- `relation_count=12`：DuckDB join-order 内部看到 12 个 relation，已经达到 large-join 阈值。
- `k_emitted=3`：按 `adl_ikkbz_k=3` 输出了 3 条 root candidate。
- `selected_order_id=ikkbz_root_0`：导出结果里的 top-1 candidate。它没有被应用到 DuckDB plan。

再从 JSON 文件里提取 ADL-OPT 后续更容易消费的候选列表：

```bash
python3 - <<'PY'
import json

with open('/tmp/adl-opt-linearization.json') as f:
    data = json.load(f)

orders = [
    {
        'linear_order_id': order['linear_order_id'],
        'order': order['order'],
    }
    for order in data['linear_orders']
]
print(json.dumps(orders, indent=2))
PY
```

在当前 smoke 上，输出类似：

```json
[
  {
    "linear_order_id": "ikkbz_root_0",
    "order": ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11"]
  },
  {
    "linear_order_id": "ikkbz_root_1",
    "order": ["r1", "r0", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11"]
  },
  {
    "linear_order_id": "ikkbz_root_2",
    "order": ["r2", "r0", "r1", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11"]
  }
]
```

这个列表就是后续 ADL-OPT runner 最应该优先读取的形态。完整 JSON 仍保留 `relation_count`、`large_join_threshold`、`edges[*].mst_edge`、`score`、`estimated_cout_rank_trace` 等调试和验证字段。这个例子里 MST 边数应该是 `relation_count - 1`，说明 selectivity MST 覆盖了所有 relation。`order` 里的 `r0`、`r1` 仍然是 DuckDB 内部 relation label，不是 SQL alias，也不是 DuckDB 实际执行时采用的 join tree。

## 手工 SQL 模板

最小使用方式是在目标查询前设置三个参数：

```sql
SET adl_linearize_join_order = true;
SET adl_linearization_output = '/tmp/adl-opt-linearization.json';
SET adl_ikkbz_k = 3;

EXPLAIN SELECT count(*)
FROM t0
JOIN t1 ON t0.i = t1.i
JOIN t2 ON t1.i = t2.i
JOIN t3 ON t2.i = t3.i
JOIN t4 ON t3.i = t4.i
JOIN t5 ON t4.i = t5.i
JOIN t6 ON t5.i = t6.i
JOIN t7 ON t6.i = t7.i
JOIN t8 ON t7.i = t8.i
JOIN t9 ON t8.i = t9.i
JOIN t10 ON t9.i = t10.i
JOIN t11 ON t10.i = t11.i;
```

不使用 `EXPLAIN` 也会触发 JSON 导出，但你就只能从文件里看结果。调试阶段建议先用 `EXPLAIN`，因为它能直接告诉你这次导出是 `ok`、`skipped_not_large_join`，还是被 regular-inner guard 标成 `unsupported`。

## JSON 怎么读

完整 JSON 的核心字段：

- `status`：导出状态。`ok` 表示拿到了可用 linear order；`skipped_not_large_join` 表示 relation 数小于 12；`unsupported` 表示当前可重排子图没有通过 R5 的 regular inner pair graph guard；`export_error` 只会出现在 EXPLAIN summary 中，表示文件写出失败。
- `relation_count`：DuckDB join-order 内部看到的 relation 数量。
- `large_join_threshold`：当前 large join 阈值，R5 使用 DuckDB 的 `PlanEnumerator::THRESHOLD_TO_SWAP_TO_APPROXIMATE`。
- `k_requested` / `k_emitted`：请求和实际输出的候选数量。
- `relations`：内部 relation id、internal label、base cardinality。
- `edges`：regular pair join edge。`selectivity` 越小表示估计选择性越强；`mst_edge=true` 表示这条边进入了 selectivity MST；`cout_rank` 是当前 IKKBZ-style 排序使用的 rank metadata。
- `linear_orders`：top-k root candidates。`linear_order_id` 和 `order` 是推荐给后续 ADL-OPT runner 读取的简洁字段；`relation_id_order`、`relation_label_order`、`score`、`estimated_cout_rank_trace` 是保留给调试和验证的详细字段。
- `selected_order_id`：top-1 candidate id。它只是导出结果里的推荐项，不会被 DuckDB 应用到 plan。

注意：`linear_orders` 是“线性候选顺序”，不是 DuckDB 最终选择的 join tree，也不是已经完成 endpoint append 后的 ADL-OPT path。

## 测试说明

文档级和静态检查：

```bash
git diff --check
python3 -m py_compile scripts/adl_opt/offline_large_join_harness.py scripts/adl_opt/offline_tpch_harness.py
```

构建检查：

```bash
CPU_COUNT=$(nproc 2>/dev/null || sysctl -n hw.ncpu)
BUILD_JOBS=$(( CPU_COUNT * 75 / 100 ))
[ "$BUILD_JOBS" -lt 1 ] && BUILD_JOBS=1
CMAKE_BUILD_PARALLEL_LEVEL=$BUILD_JOBS make reldebug
```

R5 large join smoke：

```bash
./build/reldebug/duckdb /tmp/adl-opt-r5-smoke.duckdb \
  < scripts/adl_opt/r5_ikkbz_linearization_smoke.sql

python3 -m json.tool /tmp/adl-opt-linearization.json >/tmp/adl-opt-linearization.pretty.json
```

期望结果：

- 默认关闭 setting 时，同一查询的 `EXPLAIN` 不应该出现 `adl_join_linearization`。
- 开启 `adl_linearize_join_order=true` 后，12 表 inner join 的 `EXPLAIN` 应该出现 `adl_join_linearization`。
- smoke JSON 中 `status` 应该是 `ok`。
- `relation_count` 应该是 `12`。
- `k_emitted` 在 `adl_ikkbz_k=3` 时应该是 `3`。
- `linear_orders[*]` 应该包含 `linear_order_id` 和 `order`。
- `linear_orders[0].relation_id_order` 长度应该等于 `relation_count`。
- `edges` 中 `mst_edge=true` 的数量应该是 `relation_count - 1`。

边界测试：

```sql
SET adl_linearize_join_order = true;
SET adl_linearization_output = '/tmp/adl-opt-linearization-small.json';

EXPLAIN SELECT count(*)
FROM t0
JOIN t1 ON t0.i = t1.i
JOIN t2 ON t1.i = t2.i;
```

这个小查询应该导出 `skipped_not_large_join`，因为 relation 数小于 12。

复杂 join 不再作为 R5 smoke 的验收对象。比如 LEFT JOIN、ASOF、MARK/SINGLE 或 hyper-edge 查询，DuckDB 查询本身仍按原生 optimizer 执行；R5 只在当前 join-order 子图能被看成 regular inner pair graph 时导出结果。实验 workload 应该从 SQL 侧避开这些形态。

## 常见误解

- “EXPLAIN 里有 `selected_order_id`”不表示 DuckDB 用了这个 order；它只是导出 metadata。
- `adl_ikkbz_k=3` 不表示会尝试 3 个物理 plan；它只导出 3 个 root linearization candidates。
- cyclic graph 会先抽 selectivity MST 供 IKKBZ 使用，但 JSON 的 `edges` 会保留可见的 regular pair edges，并用 `mst_edge` 标记哪些边进入了 MST。
- 当前 internal label 不是 SQL alias。后续如果要把导出结果交给外部 harness，需要另做 alias/relation mapping。
- R5 不是 DuckDB 复杂 join 的通用 legality checker。复杂 join 支持要走单独的 constraint model / hypergraph export 设计。
