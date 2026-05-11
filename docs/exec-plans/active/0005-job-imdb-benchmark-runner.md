# 0005 JOB/IMDB Benchmark Runner

English TL;DR: Add an executable classic JOB/IMDB runner that measures correctness, SQL-to-plan latency, and execution latency for large regular inner pair join queries.

Updated: 2026-05-10

Key terms: JOB, IMDB, benchmark runner, plan latency, execution latency, correctness, P99

## Goal

本轮把 ADL-OPT 的主 benchmark 从静态 artifact 推进到可执行 JOB/IMDB 回路。TPC-H 继续做开发 smoke；论文主验证开始落到 classic JOB/IMDB 的 `n >= 12` large join 查询上。

runner 只选择能被当前静态 parser 表达为 connected regular inner pair graph 的 SQL。复杂 join、JOBLight adapter、真实 endpoint order 应用都不塞进这一轮。

## Non-goals

- 不扩展 `offline_large_join_harness.py`，它继续做静态 graph/endpoint artifact。
- 不下载或构建 IMDB 数据库，只验证用户提供的 DuckDB database 是否含所需表。
- 不做 C++ instrumentation；第一版使用 DuckDB detailed profiling 读取 planner/optimizer/physical planner 阶段时间。
- 不把 IKKBZ/NeuSO 输出应用到 DuckDB 最终 plan。
- 不接 JOBLight。

## Command

Static shape check:

```bash
python3 scripts/adl_opt/job_benchmark_runner.py \
  --output /tmp/adl-opt-job-static \
  --run-id smoke \
  --queries 29a 28a 33a \
  --random-endpoint-paths 2
```

Executable classic JOB/IMDB smoke:

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

## Outputs

```text
adl-opt-runs/<run_id>/
  run_config.json
  workload.jsonl
  query_graph.jsonl
  variant.jsonl
  plan_result.jsonl
  run_result.jsonl
  correctness.jsonl
  summary.json
  summary.md
  traces/
  profiles/
```

`plan_result.jsonl` 记录 DuckDB detailed profiling 中的 SQL 到 logical/optimized/physical plan 阶段时间，不把 CLI 子进程启动计入正式 plan latency。`EXPLAIN` 每个 variant 只执行一次，用于生成 `explain_hash` 和确认 plan 可生成。`run_result.jsonl` 记录 profile `latency - plan phases` 得到的 physical execution 时间，用来代表 plan quality；外部 wall-clock 只作为诊断字段保留。

为了降低测量噪声，runner 会把同一个 variant 的 warmup/measure 放进同一个 DuckDB CLI session 内执行，而不是每个 sample 启动一个子进程。主 benchmark 采用 warm-cache 口径：同一 session 先 warmup，再 measure。cold-cache 需要 OS cache 控制，不作为本阶段验收标准。

为了保护本地 WSL 磁盘，executable runner 默认给 DuckDB 设置受控 temp 目录、`max_temp_directory_size=8GB`、`max_memory=4GB`。每批 benchmark 后都应该检查 temp 目录；如果出现 timeout 或手动中断，先确认没有 DuckDB 进程再清理遗留 temp 文件。

`sql_original` 是参考 baseline：它关闭 DuckDB join-order optimizer 后执行原 SQL，可能失败或 timeout；这些 case 会被记录到 failure/timeout 统计，不阻断整轮 benchmark。valid random endpoint path 会被改写成显式 `JOIN ... ON ...` tree，并通过 `disabled_optimizers='join_order'` 尝试固定执行。IKKBZ top-1 和 NeuSO runtime variant 当前只打开验证/导出 setting，DuckDB 仍选择最终 plan。

## Validation

- `python3 -m py_compile scripts/adl_opt/job_benchmark_runner.py` passes.
- Static run writes all expected files under `<output>/<run_id>/`.
- JSONL files parse line by line.
- With `--execute`, default variant records row count and checksum for each selected query.
- Executable variants record plan latency P50/P95/P99/max and execution latency P50/P95/P99/max.
- Correctness failures, timeout count, fallback/skipped count, speedup/regret, and win/loss count appear in `summary.json`.

## Acceptance

第一版验收不要求 ADL-OPT 赢 DuckDB；它只要求测评组织是干净的：

- selected workload 明确是 classic JOB/IMDB large connected regular inner pair queries。
- plan latency 和 execution latency 分开记录。
- correctness gate 在 speedup/regret 之前执行。
- JOBLight 不进入本阶段产物和结论。
