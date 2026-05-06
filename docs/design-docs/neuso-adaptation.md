# NeuSO Adaptation for ADL-OPT

English TL;DR: ADL-OPT borrows NeuSO's state-transition view, but maps graph-query states to DuckDB connected join subsets and starts with offline TPC-H experiments.

Updated: 2026-05-06

Key terms: NeuSO, CCG, cardinality-cost graph, minimum cost, top-down enumerator, transition

## NeuSO 中最值得迁移的思想

NeuSO 的关键不是“用了 GNN”，而是把优化问题重写成状态图上的路径搜索：

- state：一个 connected partial query。
- transition：加入一个新 vertex/relation。
- cost：一次 transition 的执行代价。
- minimum cost：从空状态到某 state 的最佳路径代价。
- enumerator：不用完整 DP 枚举所有状态，而是用 learned cost/min-cost 做 top-down search。

ADL-OPT 在 DuckDB 上可以复用这个抽象，把 relation 当作 vertex，把 join predicate 当作 edge。

## DuckDB 映射

| NeuSO | ADL-OPT on DuckDB |
| --- | --- |
| query graph | SQL join graph |
| query vertex | table alias / base relation |
| query edge | join predicate |
| connected subquery | connected join subset |
| matching order | join order |
| transition | append adjacent relation |
| cardinality | DuckDB estimate or measured row count |
| transition cost | variant runtime/profiling metric |
| minimum cost | sampled oracle or full enumeration best prefix cost |

## v0 简化

NeuSO 的完整模型包含 query graph encoder、cardinality predictor、cost predictor 和 top-down enumerator。ADL-OPT v0 只实现数据和接口层：

- 先收集 state/transition/run_result JSONL。
- 先用 heuristic/random/sampled oracle 产生 decisions。
- 模型训练只定义输入输出 schema，不要求接入 DuckDB。
- 允许后续用 PyTorch 在 GPU 上训练轻量 ranker、MLP 或 comparator。

## 训练数据策略

小查询可以 full exploration。较大查询采用 partial exploration：

- 从 DuckDB default plan、SQL 原始顺序、cardinality heuristic、random valid orders 生成候选路径。
- 记录路径上的 states 和相邻 transition。
- 对可承受的 query 再采样额外 transition，近似 oracle。

这种策略对齐 NeuSO 的 fully explored / partially explored 数据划分，但适配关系型查询。

## 后续研究问题

- DuckDB 的 estimated cardinality 是否足以作为 state feature。
- fixed-order SQL 是否稳定表达 join order。
- lightweight comparator 是否能在 TPC-H 小规模上降低 regret。
- top-down selection 是否比随机和简单 cardinality heuristic 更稳。
