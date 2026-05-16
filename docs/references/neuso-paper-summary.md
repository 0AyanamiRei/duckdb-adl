# NeuSO Paper Summary

English TL;DR: NeuSO is a neural optimizer for subgraph query matching order, not a general SQL optimizer. It represents connected subqueries as states in a Cardinality-Cost Graph, learns cardinality, state minimum cost, and transition/WCOJ cost, then uses a top-down enumerator to output a linear vertex matching order.

Updated: 2026-05-14

Key terms: NeuSO, subgraph query, matching order, CCG, cardinality-cost graph, state minimum cost, transition cost, WCOJ, TriAT, top-down enumerator

## 来源与范围

本文只总结 NeuSO 论文和本地 `NeuSO/` 模型代码体现出的算法本体，不讨论 DuckDB、ADL-OPT、sidecar bridge 或当前工程接入方向。

需要注意：`docs/references/Neo.pdf` 是 Neo 论文，不是 NeuSO 论文。Neo 研究关系型数据库的 learned query optimizer；NeuSO 研究图数据库里的 subgraph query matching order，二者不是同一个问题。

本地可核对的 NeuSO 代码入口：

- `NeuSO/Readme.md`：数据图、查询图、filter graph 和 CCG 文件格式。
- `NeuSO/Graph/graph.py`：query graph 和 filter count 的读取与初始特征构造。
- `NeuSO/Graph/ccg.py`：CCG state、transition、cardinality、minimum cost 和 transition cost 的读取。
- `NeuSO/Model/GNN.py`：query graph encoder，包括 TriAT / GIN / GAT。
- `NeuSO/Model/PredictModel.py`：用于预测 cost/cardinality 的 MLP。
- `NeuSO/Plan/plan_enumerator.py`：基于 learned scores 的 top-down matching-order enumerator。

## 研究问题

NeuSO 解决的是 subgraph query optimization 中的 matching order 选择问题。

给定一个数据图和一个查询图，系统需要决定按什么顺序匹配查询图中的 vertex。不同 matching order 会导致中间候选集大小和执行代价差异很大。传统完整 DP 会面对很大的状态空间；NeuSO 的核心思想是把这个搜索空间表示为 Cardinality-Cost Graph，然后用学习模型估计状态和转移代价，避免完整枚举。

这里的输出是一个查询图 vertex 的线性 matching order。它不是 SQL physical plan，不包含 scan path、join algorithm、projection、aggregation，也不表达 bushy tree。

## 输入

NeuSO 的输入不是 SQL 文本，而是图查询优化所需的结构化图数据。

数据图：

- 是否有向。
- vertex 数、edge 数。
- vertex label 数、edge label 数。
- 每个 vertex 的 label。
- 每条 edge 的 source、target 和 label。

查询图：

- 查询 vertex。
- 查询 edge。
- vertex label。
- edge label。

Filter graph：

- 每个查询 vertex 的候选数量 `C(v)`。
- 每条查询 edge 的候选边数量 `C(u, v)`。

CCG 训练数据：

- CCG node 表示一个 connected subquery/state。
- CCG node 记录该 state 覆盖的查询 vertex 集合。
- CCG node 可带 state cardinality。
- CCG node 可带 state minimum cost。
- CCG edge 表示从一个较小 state 扩展到较大 state 的 transition。
- CCG edge 记录加入的查询 vertex 和该 transition 的执行代价。
- CCG 文件区分 total exploration 和 partial exploration。

本地代码里，query graph encoder 先给每个查询 vertex 生成 embedding；某个 state 的 embedding 由该 state 中所有 vertex embedding 求和得到。transition feature 通常由 child-state embedding 和 parent-state embedding 拼接得到。

## 模型输出

NeuSO 的模型不是直接“一步输出最终计划”。它学习三个与搜索相关的数值信号：

- `state_card_model`：预测 state/subquery cardinality。
- `state_cost_model`：预测 state minimum cost，即到达该 state 的较优累计代价。
- `wcoj_cost_model`：预测一次 transition/WCOJ operation 的代价。

这些预测头在代码中是 MLP。训练时通常预测 log value，再用 log-space loss 监督 cardinality 和 cost。NeuSO 还可以加入 consistency loss，让 state minimum cost 与可选 transition cost 之间保持合理关系。

## Enumerator 如何生成 order

NeuSO 的 plan enumerator 是 top-down 的。

它从完整查询图 vertex 集合开始。每一步考虑移除一个 vertex，但只有当移除后剩余 vertex 仍然 connected 时，这个 vertex 才是合法候选。对每个候选，enumerator 计算：

```text
score = predicted_transition_cost(parent_state -> child_state)
      + predicted_state_minimum_cost(child_state)
```

然后选择 score 最小的候选 vertex 移除。不断重复直到 state 只剩一个 vertex。最后把移除序列反转，得到完整 matching order。

因此，NeuSO 最终产物是一个线性 vertex order。例如：

```text
[v3, v1, v4, v0, v2]
```

这个 order 的语义是先匹配 `v3`，再匹配与当前已匹配部分连通的 `v1`，然后继续追加其他 vertex。它类似 join-order 语境中的 left-deep append order，但在 NeuSO 论文语境里应称为 subgraph matching order。

## 能处理的查询形态

NeuSO 适合处理能表示成查询图的 subgraph query：

- 查询由 vertex 和 edge 组成。
- predicate 主要体现为 vertex label、edge label、候选 vertex count、候选 edge count。
- matching order 可以被表达成逐步追加查询 vertex 的线性顺序。
- 中间状态可以被表示为 connected subquery。

如果用 SQL 作类比，NeuSO 最接近的是 conjunctive inner-join graph：

```text
relation/table alias -> query vertex
pairwise join predicate -> query edge
connected partial join -> state
append one adjacent relation -> transition
linear join order -> matching order
```

这个类比只用于理解 NeuSO 的抽象能力，不表示 NeuSO 本身能解析或优化完整 SQL。

## 不处理的内容

NeuSO 本身不处理以下问题：

- SQL parsing、binding、rewrite。
- outer join、semi join、anti join、NULL-preserving semantics。
- correlated subquery、LATERAL、dependent join。
- 多表复杂 predicate 或无法投影成普通 graph edge 的 constraint。
- join algorithm 选择，例如 hash join、nested loop join、merge join。
- access path、index selection、scan strategy。
- bushy join tree 生成。
- projection、aggregation、window、order by、limit 等完整 SQL plan 问题。

这些限制来自问题定义：NeuSO 是 subgraph query matching-order optimizer，而不是通用数据库查询优化器。

## 与 IKKBZ 的关系

如果只看最终产物，NeuSO 和 IKKBZ 都可以产生一个线性 order。但二者本质不同：

- IKKBZ 是基于固定公式和代价/选择率估计的传统排序算法。
- NeuSO 是 learned scorer 加 top-down enumerator。
- IKKBZ 直接按规则构造 order。
- NeuSO 通过 state cardinality、state minimum cost 和 transition cost 的预测来引导搜索。

所以“最终都是 linear order”是对输出形态的描述；NeuSO 的研究重点在于如何学习 state/transition 代价，并用这些预测减少完整状态图枚举。

## 核心结论

NeuSO 的角色可以概括为：

```text
query graph + filter counts + CCG labels
  -> query graph encoder
  -> state/transition cost and cardinality predictors
  -> top-down enumerator
  -> linear subgraph matching order
```

它最值得关注的是 CCG state-transition 建模方式，而不是把它误解为一个能直接接管完整 SQL join-order 优化的通用模型。
