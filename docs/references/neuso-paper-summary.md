# NeuSO Paper Summary

English TL;DR: NeuSO reframes subgraph query planning as path search on a cardinality-cost graph, predicts subquery cardinality/cost/minimum cost, and uses a top-down enumerator to avoid exhaustive DP.

Updated: 2026-05-06

Key terms: NeuSO, CCG, cardinality-cost graph, TriAT, minimum cost, top-down enumerator, subgraph query

## 来源

Local file: `adl-opt/NeuSO.pdf`

Paper: NeuSO: Neural Optimizer for Subgraph Queries.

## 中文摘要

NeuSO 针对子图查询优化。传统子图查询优化通常需要选择 matching order，复杂查询下完整 DP 状态空间很大。NeuSO 的贡献是：

- 用 query graph encoder 生成 query/subquery 表示。
- 同时预测 subquery cardinality 和 execution cost。
- 引入 state minimum cost。
- 用 top-down plan enumerator 选择 matching order，减少完整状态图枚举。
- 在训练中区分 fully explored 和 partially explored queries。

## 对 ADL-OPT 的启发

ADL-OPT 在 DuckDB 关系查询里可以借用 NeuSO 的状态图视角：

- relation set 是 state。
- append relation 是 transition。
- join graph connectivity 限制 transition 合法性。
- DuckDB profiling 提供 transition/path cost label。
- 小查询 full exploration，大查询 partial exploration。

v0 不需要立即实现 TriAT。更重要的是先把 CCG-like 数据收集和 transition label 做稳。

## 与 ADL-OPT 的差异

- NeuSO 面向 graph database/subgraph matching；ADL-OPT v0 面向 DuckDB relational joins。
- NeuSO 直接生成 matching order；ADL-OPT v0 先生成离线 SQL variants。
- NeuSO 使用 query graph GNN；ADL-OPT v0 可以先用简单结构特征和 DuckDB estimates。
