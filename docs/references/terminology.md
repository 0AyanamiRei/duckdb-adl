# ADL-OPT Terminology

English TL;DR: Shared vocabulary for ADL-OPT agents and paper writing.

Updated: 2026-05-06

Key terms: terminology, hint, state, transition, CCG, join graph

## 术语表

| Term | 中文 | Meaning in ADL-OPT v0 |
| --- | --- | --- |
| ADL-OPT | ADL 优化器 | The research project for learned hint/join-order optimization on DuckDB |
| Harness | 研究框架/实验框架 | Versioned docs, plans, scripts, artifact schemas, and validation around the project |
| Hint | 提示词/提示 | A choice to append one adjacent relation to the current connected join subset |
| Hint path | 提示路径 | A complete valid join order |
| Join graph | 连接图 | Graph where nodes are table aliases and edges are join predicates |
| State | 状态 | A connected subset of aliases in the join graph |
| Transition | 状态转移 | Adding one adjacent alias to a state |
| Large join | 大连接查询 | A join-order problem with more than 12 reorderable relations in the current ADL-OPT scope |
| Linear order | 线性顺序 | A sequence of relations produced before endpoint-append decisions; the real linearization algorithm is not implemented yet |
| Endpoint append | 端点追加 | Adding the left or right endpoint relation next to the current interval in a linear order |
| CCG | Cardinality-Cost Graph | NeuSO term for a graph of connected partial queries and transition costs |
| Cardinality | 基数 | Estimated or actual row count of a relation/subplan/state |
| Cost | 代价 | Runtime/profiling metric or estimate used to compare decisions |
| Minimum cost | 最小代价 | Best known cost from empty state to a state |
| Plan control | 计划控制 | Evidence that a SQL variant produced the intended join tree |
| Regret | 后悔值 | Difference from sampled oracle or best observed variant |
| Sampled oracle | 采样 oracle | Best observed result among sampled valid orders, not a true global optimum |

## 命名约定

- Query ids use `tpch_q03`, `tpch_q05`, etc.
- JOB/IMDB query ids use `job_29a`, `job_28a`, etc.
- State ids sort aliases alphabetically and join with `+`.
- Linear-order interval state ids include `query_id`, `linear_order_id`, and `i<left>-<right>`.
- Transition ids combine `from_state_id` and `added_alias`.
- Variant ids include query id, baseline kind, and index or seed.
