# ADL-OPT Core Beliefs

English TL;DR: ADL-OPT should improve query optimization by learning which connected join transition to choose next, while preserving DuckDB as the trusted execution engine.

Updated: 2026-05-06

Key terms: ADL-OPT, learned optimizer, hint, connected state, transition, join order, comparator

## 研究信念

ADL-OPT 的核心目标不是重写 DuckDB 优化器，而是在保留传统优化器稳定性的前提下，学习更好的搜索决策。

第一阶段采用更窄的定义：

- 状态是一个 connected join subset。
- transition 是向当前状态追加一个相邻 relation。
- hint 是 transition 选择。
- hint path 是完整 join order。
- reward/cost 来自 DuckDB 实际执行和 profiling。

这个定义把论文中的“提示词选择问题”落到 DuckDB 可观测的 join-order 问题上。

## 为什么不用全局规则开关做 v0 Hint

DuckDB 的 `disabled_optimizers` 很适合做对照实验，但它不是 ADL-OPT v0 的主 hint，因为：

- 它作用粒度是全局 optimizer pass，不是查询计划局部状态。
- 它难以表达 NeuSO/ADL-OPT 关注的子状态 transition。
- 它更像系统 ablation，而不是 join plan search。

全局规则开关可以保留为后续 baseline 或鲁棒性实验。

## 成功标准

v0 成功不是“模型上线”，而是形成稳定研究闭环：

- 能从 TPC-H 查询得到 join graph。
- 能枚举 connected states 和 valid transitions。
- 能生成多个固定 join-order SQL variant。
- 能验证结果正确性和计划控制。
- 能收集足够训练轻量 ranker/comparator 的 JSONL。

## 非目标

- 不改 DuckDB C++ optimizer。
- 不新增 DuckDB public API。
- 不做在线学习或后台重采样。
- 不追求完整 TPC-H/TPC-DS/JOB 覆盖。
- 不把 GPU 训练放进查询执行路径。
