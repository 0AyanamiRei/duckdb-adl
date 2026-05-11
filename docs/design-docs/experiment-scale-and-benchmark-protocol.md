# Experiment Scale and Benchmark Protocol

English TL;DR: ADL-OPT uses a staged benchmark protocol: tiny TPC-H for development, TPC-H SF 0.1/SF 1 for validation, executable classic JOB/IMDB for the main thesis benchmark, and larger scale factors only as optional stress tests.

Updated: 2026-05-10

Key terms: benchmark protocol, dataset scale, TPC-H, JOB, IMDB, smoke test, main experiment

## Purpose

本文档固定 ADL-OPT 第一阶段的测试轮次、数据集规模和资源预估。目标不是追求精确到 MB 的容量预测，而是让每一轮实验都有明确的输入规模、运行目的、磁盘预算和准入标准，避免把项目拖成不可复现的数据搬运工程。

ADL-OPT 早期 R0/R1 只研究 offline join-order decision harness；现在的 applied path 会在实验 setting 下修改 DuckDB large-join 子图的 chosen join plan。所有性能结论都必须建立在结果正确、计划形状可验证、实验配置可复现的前提上。

## Scale Ladder

| Round | Goal | Dataset | Scale | Approximate Disk Budget | Main Use |
|---|---|---:|---:|---:|---|
| R0 | Harness development | Mock/static query graph | 0 | <10 MB | SQL graph extraction, connected state enumeration, JSONL schema checks |
| R1 | Minimal smoke | TPC-H | SF 0.01 | 10-30 MB | Verify DuckDB execution, checksum, profiling, and fixed-order SQL generation |
| R2 | Routine development | TPC-H | SF 0.1 | 100-300 MB | Validate Q3/Q5/Q8/Q9/Q10 with default/original/heuristic/random orders |
| R3 | Local validation | TPC-H | SF 1 | 1-3 GB | Measure whether join-order choices create meaningful execution-time differences |
| R4 | Main thesis benchmark | Classic JOB/IMDB | Fixed JOB data | 1-3 GB | Evaluate plan latency and plan quality on larger join graphs and skewed real-world-style predicates |
| R5 | Optional stress | TPC-H | SF 10 | 10-30 GB | Stress a small query subset after the harness is stable |

这些预算按本机 DuckDB 落库、profiling 文件、JSONL artifact 和少量中间结果预留。不同文件系统、压缩格式和临时目录配置会造成波动；实验报告中必须记录实际数据库文件大小和 artifact 目录大小。

## Recommended Progression

R0 是默认开发入口。它不依赖 DuckDB binary，也不要求真实数据，只要求静态 runner 能生成 `query_graph.jsonl`、`state.jsonl`、`transition.jsonl`、`run_result.jsonl` 和 `decision.jsonl`。

R1 使用 TPC-H SF 0.01，优先跑 Q3、Q5、Q8。每个 query 至少生成 DuckDB default、SQL original、cardinality heuristic 和 5 个 random valid connected orders。R1 的目标是 1-5 分钟内完成一轮，并暴露 SQL 生成、checksum、profiling、timeout 和 summary 逻辑的问题。

R1 标准执行命令：

```bash
python3 scripts/adl_opt/offline_tpch_harness.py \
  --duckdb ./build/reldebug/duckdb \
  --database /tmp/adl-opt-r1-tpch.duckdb \
  --output /tmp/adl-opt-r1 \
  --queries q03 q05 q08 \
  --scale-factor 0.01 \
  --random-orders 5 \
  --execute \
  --threads 1 \
  --warmup-runs 1 \
  --measure-runs 5 \
  --timeout 120
```

R2 使用 TPC-H SF 0.1，覆盖 Q3、Q5、Q8、Q9、Q10。每个 query 至少生成 DuckDB default、SQL original、cardinality heuristic、20 个 random valid connected orders 和 sampled oracle best order。R2 是功能稳定性的主要门槛。

R3 使用 TPC-H SF 1。它不要求全量 TPC-H，只要求 join-heavy 查询子集。R3 用来判断 join-order variant 的性能差异是否大到足够写进论文，而不是只证明 harness 可以运行。

R4 使用 classic JOB/IMDB 作为主论文 benchmark。仓库中已有 JOB/IMDB 查询和答案文件，加载脚本会读取 21 个 parquet 数据源。第一版主实验优先筛选 `n >= 12` 的 connected regular inner pair graph 查询；不要求一次覆盖全部 JOB 查询。JOBLight 暂时不做 adapter，也不作为第一阶段验收内容。

Large-join 静态入口先固定为 JOB/IMDB 29/28/33 系列：

```bash
python3 scripts/adl_opt/offline_large_join_harness.py \
  --output /tmp/adl-opt-large-static \
  --queries 29a 29b 29c 28a 28b 28c 33a 33b 33c \
  --random-paths 5
```

这一步不加载 JOB 数据，也不执行 DuckDB；它只验证 SQL graph extraction、fixture linear order、linear interval state 和 endpoint append transition。

Classic JOB/IMDB 可执行 benchmark 入口是单独的 runner：

```bash
python3 scripts/adl_opt/job_benchmark_runner.py \
  --duckdb ./build/reldebug/duckdb \
  --database /path/to/imdb.duckdb \
  --output /tmp/adl-opt-runs \
  --run-id job_r1_smoke \
  --queries 29a 29b 29c 28a 28b 28c 33a 33b 33c \
  --execute \
  --threads 1 \
  --temp-directory /home/refrain/data/adl-opt/job-imdb/tmp \
  --max-temp-directory-size 8GB \
  --max-memory 4GB \
  --warmup-runs 1 \
  --measure-runs 3
```

这个 runner 不负责下载或完整构建 IMDB 数据库；它只验证目标表是否存在，然后执行 selected queries。输出采用 `adl-opt-runs/<run_id>/` 结构，包含 `workload.jsonl`、`variant.jsonl`、`plan_result.jsonl`、`run_result.jsonl`、`correctness.jsonl`、`summary.json/md`、`traces/` 和 `profiles/`。

本地磁盘预算必须显式管控。JOB 查询可能产生很大的 DuckDB temp spill；runner 默认设置 `<database>.tmp-safe`、`max_temp_directory_size=8GB`、`max_memory=4GB`，但正式实验命令仍建议显式传入这些参数，并在每批运行后检查 temp 目录大小。

valid random endpoint path 会被改写成显式 `JOIN ... ON ...` tree，并用 `disabled_optimizers='join_order'` 测量。IKKBZ top-1 和 NeuSO runtime variant 当前只验证导出/sidecar 通路，不把返回 order 应用到最终 plan。

R5 是可选压力实验。只有在 R2/R3/R4 都稳定后，才选择少量 TPC-H SF 10 查询运行。R5 不作为毕设通过条件。

## Workload Selection

TPC-H smoke/development 查询固定为：

- Q3
- Q5
- Q8
- Q9
- Q10

其中 Q3、Q5、Q8 是最小验收子集。Q9 和 Q10 用于扩大 join 图与谓词形态覆盖。

Classic JOB/IMDB 主实验应优先选择：

- 只包含 inner join 或能被 DuckDB 当前 join-order optimizer 处理的 comparison join。
- join relation 数量大于 12，能触发 DuckDB large-join approximate path。
- 谓词选择性有差异，避免所有 order 性能几乎一致。
- DuckDB default、original order 和随机合法 order 都能稳定通过 correctness check。

第一阶段不把 outer join、ASOF、MARK、SINGLE、dependent/delim join 或复杂 correlated subquery 作为主要 workload。

第一批固定查询：

- 29a, 29b, 29c.
- 28a, 28b, 28c.
- 33a, 33b, 33c.

## Benchmark Modes

计划控制验证模式用于确认固定 join order 没被 DuckDB 改形：

```sql
SET disabled_optimizers='join_order,build_side_probe_side';
```

性能测量模式用于模拟“只替换 join-order optimizer”的环境：

```sql
SET disabled_optimizers='join_order';
```

所有主实验默认使用单线程，降低噪声：

```sql
SET threads=1;
```

必要时可以添加多线程补充实验，但不能替代单线程主结果。

编译 DuckDB 时默认只使用约 75% CPU，满足 70-80% 的本机资源限制：

```bash
CPU_COUNT=$(nproc 2>/dev/null || sysctl -n hw.ncpu)
BUILD_JOBS=$(( CPU_COUNT * 75 / 100 ))
[ "$BUILD_JOBS" -lt 1 ] && BUILD_JOBS=1
CMAKE_BUILD_PARALLEL_LEVEL=$BUILD_JOBS BUILD_TPCH=1 make reldebug
```

## Baselines

每个 query 至少比较：

- DuckDB default optimizer.
- SQL original join order.
- Cardinality heuristic order.
- Random valid connected orders.
- Sampled oracle best order.
- ADL-OPT scorer selected order.

Large-join endpoint 实验额外比较：

- DuckDB current approximate greedy.
- IKKBZ top-1 export validation, DuckDB plan unchanged.
- NeuSO runtime validation, DuckDB plan unchanged.
- Fixture linear order plus ADL-OPT endpoint path.
- Random endpoint paths over the same linear order.

Random order 数量按轮次递增：

- R1: 5 per query.
- R2: 20 per query.
- R3/R4: 20-100 per query, 由运行时间预算决定。

如果某个查询的候选空间很小，应记录实际候选数量，不要重复采样伪造样本量。

## Measurement Rules

JOB/IMDB 第一阶段每个 variant warm up 1 次，正式测量 3 次，并报告 P50/P95/P99/max。由于样本数较小，P95/P99 只作为本地小样本参考；如果后续写论文主结果，可以单独提高 `measure_runs`。

JOB/IMDB 主测评拆成两类 latency：

- Plan latency: 每个 query/variant 只执行一次 `EXPLAIN <query>` 来获取 `explain_hash` 和确认 plan 可生成；正式指标来自 DuckDB detailed profiling 中的 parser、planner、optimizer、physical planner 阶段时间，不使用外部子进程 wall-clock。
- Execution latency: 对每个 query/variant warmup 1 次、measure 3 次，正式指标来自 DuckDB detailed profiling 的 `latency - plan phases`，用 physical execution 时间代表 plan quality。

runner 会把同一个 variant 的 warmup/measure 放在同一个 DuckDB CLI session 里执行，不使用 prepared statement。`duckdb_wall_time_samples_ms` 只作为诊断字段，不能作为论文主结果。`sql_original` 是参考 baseline；失败或 timeout 会被记录，但不阻断整轮 benchmark，也不进入 speedup/regret 的成功样本集合。

每次运行必须记录：

- query id
- dataset id and scale factor
- SQL variant id
- join order path
- DuckDB version or commit
- optimizer settings
- thread count
- timeout
- plan latency samples and P50/P95/P99/max
- execution latency samples and P50/P95/P99/max
- optimizer time from detailed profiling if available
- physical execution time from detailed profiling if available
- EXPLAIN hash
- result row count
- result checksum
- failure reason when failed

性能 summary 至少包含：

- speedup versus DuckDB default
- regret versus sampled oracle
- plan latency P50/P95/P99/max
- execution latency P50/P95/P99/max
- optimizer time
- execution time
- failed order count
- plan valid rate

## Correctness Gates

任何 variant 进入性能统计前必须通过：

- 结果 row count 与 DuckDB default 一致。
- order-independent checksum 与 DuckDB default 一致，或排序后逐行一致。
- 固定 join order 的 EXPLAIN 证据可追溯。
- 运行未超时。

Profiling artifact 用于补充 optimizer/execution time。R1 允许 profiling 字段为 null；解析失败不应阻断 correctness。Correctness 失败或 plan-control 失败的 variant 不进入 speedup/regret 统计，但必须计入 failed order count。

## Resource Defaults

最小可复现实验资源：

- Dataset: TPC-H SF 0.1.
- Disk budget: 1 GB.
- Queries: Q3, Q5, Q8.
- Random orders: 5-20 per query.

完整本机实验资源：

- Dataset: TPC-H SF 1 plus JOB/IMDB.
- Disk budget: 8-10 GB.
- Queries: TPC-H Q3/Q5/Q8/Q9/Q10 plus selected classic JOB/IMDB queries.
- Random orders: 20-100 per query, constrained by timeout.

可选压力资源：

- Dataset: TPC-H SF 10.
- Disk budget: 30 GB or more.
- Queries: a small join-heavy subset only.

## Reporting Requirements

每轮实验结束后生成一个 summary 目录，至少包含：

- `summary.json`
- `summary.md`
- JSONL artifact files
- DuckDB settings snapshot
- dataset scale and actual disk usage
- command line used to run the experiment

实验报告中的图表必须标注数据集规模。不同 scale factor 的 latency 不能直接混在同一条结论中比较。
