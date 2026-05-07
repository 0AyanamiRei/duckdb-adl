# NeuSO Adaptation for ADL-OPT

English TL;DR: ADL-OPT borrows NeuSO's state-transition view for linearized large joins, while DuckDB's exact DPhyp remains the trusted path for n<=12 joins.

Updated: 2026-05-07

Key terms: NeuSO, CCG, cardinality-cost graph, large join, linear order, endpoint append

## NeuSO 中最值得迁移的思想

NeuSO 的关键不是“用了 GNN”，而是把优化问题重写成状态图上的路径搜索：

- state：一个 connected partial query。
- transition：加入一个新 vertex/relation。
- cost：一次 transition 的执行代价。
- minimum cost：从空状态到某 state 的最佳路径代价。
- enumerator：不用完整 DP 枚举所有状态，而是用 learned cost/min-cost 做 top-down search。

ADL-OPT 在 DuckDB 上复用这个抽象时要加一个重要边界：DuckDB 原生 exact DPhyp 已经能处理 `n <= 12` 的 bushy join search，这一段不需要 ADL-OPT 介入。NeuSO-style 的单 relation 追加更适合放在 `n > 12` 的 large-join approximate 区间，并且是在已有 linear order 上做 endpoint append 决策。

## DuckDB 映射

| NeuSO | ADL-OPT on DuckDB |
| --- | --- |
| query graph | SQL join graph |
| query vertex | table alias / base relation |
| query edge | join predicate |
| connected subquery | connected join subset |
| matching order | join order |
| transition | append one endpoint relation after large-join linearization |
| cardinality | DuckDB estimate or measured row count |
| transition cost | variant runtime/profiling metric |
| minimum cost | sampled oracle or full enumeration best prefix cost |

## Large-Join 边界

当前项目口径改为：

- `n <= 12`：继续使用 DuckDB exact DPhyp，ADL-OPT 不插手。
- `n > 12`：DuckDB 当前进入 approximate greedy pair merge，ADL-OPT 只研究这一段是否能更好。
- 线性化算法本身暂不实现，单独 issue 讨论。
- 第一版只接受一个已有 linear order，然后研究从当前连续区间的左端点或右端点追加 relation。

R5 之后，NeuSO-style endpoint append 的上游输入不再只依赖 Python fixture。DuckDB 内核可以在 large join 上导出一组 IKKBZ-style linear order candidates：

- cyclic graph 先压成 selectivity MST。
- 每个 root 产生一个线性候选。
- `debug_adl_opt_ikkbz_k` 控制导出的 root candidates 数量。
- 导出的 order 仍然只是后续 ADL-OPT 的输入，不在线上改写 DuckDB plan。

这种 endpoint append 仍保留 NeuSO 的 state-transition 学习视角，但不会声称 NeuSO 直接替代 DuckDB 的 hypergraph DP。

## v0 简化

NeuSO 的完整模型包含 query graph encoder、cardinality predictor、cost predictor 和 top-down enumerator。ADL-OPT v0 只实现数据和接口层：

- 先收集 query_graph、linear_order、state、transition、endpoint_path、run_result JSONL。
- 先用 heuristic/random/sampled oracle 产生 decisions。
- 模型训练只定义输入输出 schema，不要求接入 DuckDB。
- 允许后续用 PyTorch 在 GPU 上训练轻量 ranker、MLP 或 comparator。

## 训练数据策略

小查询可以 full exploration，但它们不是 ADL-OPT 后续主战场。较大查询采用 partial exploration：

- 从 JOB/IMDB large-join SQL 生成 query graph。
- 读取或构造 fixture linear order。
- 记录 linear interval states 和 endpoint transitions。
- 记录 fixture endpoint path 与 random endpoint path。
- 对可承受的 query 再采样额外 transition，近似 oracle。

这种策略对齐 NeuSO 的 fully explored / partially explored 数据划分，但适配关系型查询。

## 后续研究问题

- DuckDB 应如何导出 large-join query graph、estimated cardinality 和 cost feature。
- Neumann-style 线性化算法应放在 DuckDB C++ 侧还是外部 harness。
- endpoint append 模型是否比 random endpoint path 和 DuckDB 当前 approximate greedy 更稳。
- JOB/IMDB 29/28/33 系列是否足以作为第一批 n>12 验证查询。
