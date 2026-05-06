# Related Work Comparison

English TL;DR: ADL-OPT v0 should position itself as a practical, DuckDB-centered offline harness inspired by Bao, Lero, and NeuSO rather than as a full replacement optimizer.

Updated: 2026-05-06

Key terms: Neo, Bao, Lero, NeuSO, learned query optimizer, join order, hint

## 对比表

| Work | Main idea | What ADL-OPT borrows | What ADL-OPT avoids in v0 |
| --- | --- | --- | --- |
| Neo | End-to-end neural optimizer | Tree/plan-aware representation motivation | Replacing the full optimizer |
| Bao | Hint-based steering with bandit learning | Practical hint steering and experience collection | Online steering inside DuckDB v0 |
| Lero | Learn-to-rank plan comparison | Pairwise/comparator framing | Depending only on generated physical plans |
| NeuSO | CCG state-transition search with learned cost/min-cost | Endpoint-append state/transition view after large-join linearization | Full GNN/TriAT implementation and direct replacement of DuckDB DPhyp |

## ADL-OPT v0 定位

ADL-OPT v0 is best described as:

> An offline DuckDB research harness for collecting and evaluating connected join-order transition decisions, designed to support later learned hint selection.

这个定位能避免两个风险：

- 过早承诺模型效果。
- 过早侵入 DuckDB optimizer。

Large-join 后续定位更窄：

> For n<=12, keep DuckDB exact DPhyp. For n>12, study whether an external ADL-OPT endpoint policy can improve the approximate join-order path after linearization.

## 后续论文表达线索

- Engineering practicality: start outside the engine and prove data/plan control first.
- Search efficiency: transition decisions may reduce candidate generation compared with exhaustive join-order search.
- Interpretability: selected hint path is a readable join order.
- Compatibility: DuckDB remains the execution and correctness oracle.
