# ADL-OPT Paper Summary

English TL;DR: ADL-OPT proposes a prompt/hint-based learned query optimizer that selects hint subsets before plan generation, aiming to keep traditional optimizer stability while improving search decisions.

Updated: 2026-05-06

Key terms: ADL-OPT, hint comparator, learned optimizer, Bao, Lero, Neo, Thompson sampling

## 来源

Local file: `adl-opt/adl-opt.pdf`

论文题目：基于提示词的学习型数据库优化器研究。

## 中文摘要

ADL-OPT 的研究动机是传统优化器在复杂查询和动态数据环境中可能因为基数估计与成本模型误差产生次优计划。相比完全替换传统优化器，论文倾向于学习型组件通过提示词辅助传统优化器。

核心想法：

- 把执行计划选择转换为提示词子集选择。
- 对给定查询 `q` 和 hint subset `hset`，传统优化器生成计划 `F(q, hset)`。
- 学习模块比较不同 hint subset 的相对优劣，而不是直接预测绝对 latency。
- 运行后收集执行时间、资源占用等经验，形成 Experience 用于后续训练。

论文受 Neo、Bao、Lero 启发：

- Neo 展示端到端学习优化的可能性，但集成和训练代价高。
- Bao 通过 hints 增强传统优化器，更实用。
- Lero 通过比较器选择更优计划，避免绝对代价预测。

## ADL-OPT on DuckDB v0 解读

为了把论文落地到 DuckDB，v0 将 hint 具体化为 join-order transition：

- hint：从 connected join subset 追加一个相邻 relation。
- hint subset/path：完整 join order。
- comparator/ranker：比较下一步 transition 或完整 path。

这样可以先构建离线数据集，再考虑是否进入 DuckDB join-order enumerator。

## 需要保留的问题

- 原论文中的“提示词”还比较抽象，需要通过 DuckDB 实验确认最有效粒度。
- 训练目标可以是 pairwise comparator、transition ranker 或 cost/min-cost predictor。
- 线上 Experience 更新不属于 v0。
