# 大规模 Join Benchmark 调研报告

**调研日期**: 2026-05-12
**调研目的**: 了解业界对于大规模 join 查询的权威 benchmark 和研究现状

---

## 执行摘要

本报告调研了业界关于大规模 join 查询优化的 benchmark 和研究工作。当前最权威的真实数据 benchmark 是 JOB (Join Order Benchmark)，最大涉及 17 张表。学术界已经在研究 25+ 张表的优化算法，AdaptiveQO 声称能扩展到数千个 join。真实 benchmark 受限于数据集规模，而 synthetic benchmark 生成器可以创建任意规模的查询但缺乏真实性。

---

## 1. 现有权威 Benchmark

### 1.1 JOB (Join Order Benchmark)

**基本信息**:
- **发布时间**: 2015 年 (VLDB)
- **数据集**: 真实 IMDB 数据
- **查询数量**: 113 个查询
- **表数范围**: 4-17 张表
- **中位数**: 8 张表
- **平均表数**: 8.6 张表

**规模分布**:
- 4-5 张表 (小型 join): 23 个查询 (20%)
- 6-8 张表 (中型 join): 39 个查询 (35%)
- 9-11 张表 (较大 join): 31 个查询 (27%)
- 12+ 张表 (大型 join): 20 个查询 (18%)

**最复杂查询**:
- **12 张表**: 11 个查询 (24a/b, 26a/b/c, 27a/b/c, 30a/b/c)
- **14 张表**: 6 个查询 (28a/b/c, 33a/b/c)
- **17 张表**: 3 个查询 (29a/b/c) - 最复杂

**核心价值**:
- 基于真实 IMDB 数据，包含真实的数据相关性
- 能够暴露 cardinality 估计错误导致的坏 join order
- 已成为 join order 优化研究的事实标准

**论文**:
- Viktor Leis, et al. "How Good Are Query Optimizers, Really?" VLDB 2015
- [ResearchGate](https://www.researchgate.net/publication/305253105_How_Good_Are_Query_Optimizers_Really)

---

### 1.2 JOB-Complex

**基本信息**:
- **发布时间**: 2025 年 (AIDB@VLDB'25)
- **查询数量**: 30 个 SQL 查询
- **执行计划**: 近 6000 个执行计划用于 plan-selection 评估
- **表数范围**: 未明确说明，基于 JOB 扩展

**复杂度提升**:
- 引入字符串列上的 join
- 复杂过滤谓词
- 更贴近生产环境的真实场景

**性能发现**:
- 传统和学习型 cost model 在 JOB-Complex 上表现不佳
- 与最优计划相比，运行时间可达 **11 倍**差距
- 现有 benchmark 不能反映真实查询优化的许多属性
- 高估了传统和学习型优化器的性能

**核心价值**:
- 暴露了现有 benchmark 的局限性
- 提供了更真实的查询优化挑战
- 包含大量执行计划用于 plan-selection 研究

**论文**:
- "A Challenging Benchmark for Traditional & Learned Query Optimization" AIDB@VLDB'25
- [ArXiv](https://arxiv.org/abs/2507.07471)

---

### 1.3 TPC-DS

**基本信息**:
- **查询数量**: 99 个查询
- **设计目标**: 模拟真实决策支持工作负载
- **复杂度**: 包含复杂 join、聚合、子查询、窗口函数

**查询特点**:
- 模拟真实业务问题：目录销售分析、客户细分、供应链报告
- 涵盖多种查询模式和优化挑战
- Query 13, 29, 48 被认为是较复杂的查询（具体表数未明确）

**与 JOB 对比**:
- TPC-DS 更关注整体 OLAP 性能，不专注于 join order
- 查询结构复杂度高，但具体 join 表数统计不明确
- 在 "Debunking the Myth of Join Ordering" 论文中，Query 29 被用作测试案例

**应用场景**:
- 评估 OLAP 引擎的整体性能
- 测试复杂查询处理能力
- 决策支持系统 benchmark

**参考资料**:
- [TPC-DS Official](https://www.tpc.org/tpcds/)
- [Doris TPC-DS Benchmark](https://doris.incubator.apache.org/docs/4.x/benchmark/tpcds/)

---

### 1.4 TPC-H

**基本信息**:
- **表数范围**: 最大 8 张表
- **中位数**: 3 张表
- **查询数量**: 22 个查询

**局限性**:
- AdaptiveQO 论文明确指出："TPC-H join graph 太小，最大 8 个 relation、中位数 3 个 relation，因此对 large join 算法不够有挑战"
- 不适合作为 join-order 优化的主验收集
- 更适合作为 smoke test

**应用场景**:
- 基础性能测试
- 快速验证系统正确性
- 与其他系统对比的基准

---

### 1.5 LDBC SNB (Social Network Benchmark)

**基本信息**:
- **全称**: Linked Data Benchmark Council Social Network Benchmark
- **工作负载类型**: Business Intelligence workload
- **数据模型**: 社交网络图数据

**查询特点**:
- 聚合和 join 密集型复杂查询
- 触及图的大部分区域
- 包含微批量的插入/删除操作

**应用场景**:
- 图数据库性能测试
- 关系数据库的图查询能力测试
- 复杂 join 和聚合性能评估

**参考资料**:
- [LDBC SNB Official](https://ldbcouncil.org/benchmarks/snb/)
- [SNB Business Intelligence Workload](https://ldbcouncil.org/benchmarks/snb/bi/)

---

## 2. 超大规模 Join 研究

### 2.1 AdaptiveQO (SIGMOD 2018)

**基本信息**:
- **作者**: Thomas Neumann, et al. (TUM Munich)
- **发布**: SIGMOD 2018
- **核心贡献**: 自适应优化框架

**声称规模**:
- 能够扩展到**数千个 join**的查询
- 精确求解大多数常见 join 查询
- 同时保持对超大查询的扩展性

**测试数据集**:
- TPC-H
- TPC-DS
- JOB/IMDB
- LDBC
- SQLite query graphs

**关键成果**:
- 将 PostgreSQL 的 heuristic-fall-back 限制从 12 张表提升到 **25 张表**
- 在相同时间预算内保持性能
- 结合精确算法和启发式算法

**算法对比**:
- DPhyp (Dynamic Programming with Hypergraph)
- GOO (Greedy Operator Ordering)
- IKKBZ
- QuickPick
- Genetic algorithms
- Adaptive framework (提出的方法)

**核心思想**:
- 对小查询使用精确算法
- 对大查询自适应切换到启发式算法
- 动态调整优化策略

**论文**:
- "Adaptive Optimization of Very Large Join Queries" SIGMOD 2018
- [ACM DL](https://dl.acm.org/doi/10.1145/3183713.3183733)

---

### 2.2 MPDP Algorithm (2022)

**基本信息**:
- **全称**: Massively Parallel Dynamic Programming
- **发布**: 2022
- **目标**: 大规模查询的并行 join 优化

**测试规模**:
- 最多 **25 张表**
- 在真实 benchmark 查询上测试

**性能提升**:
- 比现有技术快 **10 倍以上**
- 生成的查询计划比 state-of-the-art 便宜 **7 倍**
- 使用增强启发式 (IDP₂ 和 UnionDP)

**核心贡献**:
- 大规模分析查询的并行优化
- 突破传统优化器的规模瓶颈
- 在相同时间预算内处理更多表

**论文**:
- "Efficient Massively Parallel Join Optimization for Large Queries"
- [ArXiv](https://arxiv.org/abs/2202.13511)

---

### 2.3 Debunking the Myth of Join Ordering (2025)

**基本信息**:
- **发布**: 2025 年 2 月
- **研究重点**: Join order 优化的误区

**测试方法**:
- 使用 JOB Query 29 (17 joins) 作为最复杂查询
- 生成 N=1000 个随机计划进行校准
- 在 TPC-DS、TPC-H、JOB 上进行评估

**关键发现**:
- 现代查询优化器仍可能生成比最优计划差几个数量级的 join plan
- Join order 优化对查询性能至关重要
- 少数查询 (13, 29, 48) 在 TPC-DS 中有较大方差

**论文**:
- [ArXiv](https://arxiv.org/html/2502.15181v2)

---

## 3. Synthetic Benchmark 生成器

### 3.1 SynQL Framework (2024)

**基本信息**:
- **发布**: 2024
- **目标**: 可控、可扩展的 SQL workload 合成

**生成方法**:
- 遍历数据库的外键图填充 AST (Abstract Syntax Tree)
- 保证 schema 和语法有效性
- 避免 LLM 方法的 schema hallucination 和 topological collapse

**控制参数** (配置向量 Θ):
1. **Join topology**:
   - Star (星型)
   - Chain (链式)
   - Fork (分叉)

2. **Analytical intensity**:
   - 聚合复杂度
   - 投影复杂度

3. **Predicate selectivity**:
   - 范围谓词过滤特性

**生成规模**:
- 可生成任意规模的 join 查询
- 针对分析型工作负载的核心 SQL 片段
- 多表 join + 投影 + 聚合 + 范围谓词

**性能验证**:
- 生成"近最大多样性"的工作负载
- 拓扑熵达到 1.53 bits
- 基于树的 cost model 在测试集上达到 R² ≥ 0.79
- 推理延迟低于毫秒级

**应用场景**:
- 生产日志不可用时训练查询优化器
- 隐私限制下的 benchmark 生成
- 可控的查询复杂度测试

**论文**:
- "A Controllable and Scalable Rule-Based Framework for SQL Workload Synthesis for Performance Benchmarking"
- [ArXiv](https://arxiv.org/abs/2604.08021)

---

### 3.2 其他生成方法

**传统方法**:
- 固定模板：缺乏多样性
- 随机生成：难以控制复杂度
- 基于规则：需要大量人工设计

**LLM 方法**:
- 优势：可以生成多样化查询
- 劣势：
  - Schema hallucination (生成不存在的表/列)
  - Topological collapse (生成的 join 拓扑单一)
  - 难以保证语法正确性

**SynQL 的优势**:
- 确定性方法，可重复
- 保证 schema 和语法有效性
- 参数化控制复杂度
- 避免 LLM 的常见问题

---

## 4. 学习型优化器的 Benchmark 实践

### 4.1 Neo (VLDB 2019)

**方法**:
- 深度强化学习完全替代传统优化器
- 树卷积神经网络
- 从 PostgreSQL 引导启动

**测试集**:
- JOB benchmark
- 持续学习传入查询

**性能**:
- 比传统优化器更好地处理 cardinality 估计错误
- 需要大量训练数据

**论文**:
- "Neo: A Learned Query Optimizer" VLDB 2019
- [ArXiv](https://arxiv.org/abs/1904.03711)

---

### 4.2 Bao (SIGMOD 2021)

**方法**:
- 基于提示的引导 (hint-based steering)
- Thompson 采样 (多臂老虎机)
- 不替代现有优化器，而是增强

**测试集**:
- JOB benchmark
- PostgreSQL 和商业数据库

**性能**:
- 2 小时学习后匹配专家优化器性能
- Workload runtime 提升 **2.8 倍**
- 学习速度比以前的方法快一个数量级

**核心优势**:
- 保留现有优化器知识
- 快速学习和适应
- 优雅降级

**论文**:
- "Bao: Learning to Steer Query Optimizers" SIGMOD 2021
- [ACM DL](https://dl.acm.org/doi/10.1145/3448016.3452838)

---

### 4.3 Lero (VLDB 2023)

**方法**:
- Learning-to-rank 方法
- 成对比较计划 (pairwise comparison)
- 二元分类而非代价回归

**测试集**:
- JOB benchmark on PostgreSQL
- 多个真实工作负载

**性能**:
- 执行时间减少高达 **70%**
- 比代价预测更鲁棒
- 对 cardinality 估计错误更稳定

**核心思想**:
- "计划的相对顺序或排名，而不是精确的代价或延迟，对于查询优化就足够了"
- 二元分类比回归更容易且更准确

**论文**:
- "Lero: applying learning-to-rank in query optimizer" VLDB 2023
- [Springer](https://link.springer.com/article/10.1007/s00778-024-00850-3)

---

### 4.4 Balsa (SIGMOD 2022)

**方法**:
- 无需专家演示的学习
- 从简单的环境无关模拟器学习基础知识
- 在真实执行中安全学习

**测试集**:
- JOB benchmark
- 开源和商业查询优化器

**性能**:
- 2 小时学习后匹配两个专家查询优化器
- Workload runtime 提升 **2.8 倍**

**论文**:
- "Learning a Query Optimizer Without Expert Demonstrations" SIGMOD 2022
- [ArXiv](https://arxiv.org/abs/2201.01441)

---

### 4.5 NeuSO (SIGMOD 2026)

**领域**:
- 图数据库子图匹配优化
- 不同于关系数据库 join order

**方法**:
- 多任务监督学习
- 查询图编码器 + 多任务估计器
- 自顶向下枚举器

**预测目标**:
1. 子查询基数
2. 执行代价
3. 最小可达代价 (novel)

**规模处理**:
- 图查询涉及的 join 比关系查询多得多
- 自顶向下枚举避免穷举搜索
- 部分探索训练策略

**论文**:
- "Neural Optimizer for Subgraph Queries" SIGMOD 2026
- [ArXiv](https://arxiv.org/abs/2509.23775)

---

## 5. 规模对比总结

### 5.1 真实 Benchmark 规模

| Benchmark | 最大表数 | 中位数 | 查询数量 | 数据集 | 年份 |
|-----------|---------|--------|---------|--------|------|
| TPC-H | 8 | 3 | 22 | 合成 | 1999 |
| JOB | 17 | 8 | 113 | 真实 IMDB | 2015 |
| TPC-DS | 未明确 | 未明确 | 99 | 合成 | 2015 |
| JOB-Complex | 未明确 | 未明确 | 30 | 真实 IMDB | 2025 |
| LDBC SNB | 未明确 | 未明确 | 多个 | 合成社交网络 | 持续更新 |

---

### 5.2 研究算法测试规模

| 工作 | 声称/测试规模 | 实际验证 | 年份 |
|------|--------------|---------|------|
| AdaptiveQO | 数千个 join | PostgreSQL 12→25 表 | 2018 |
| MPDP | 25 张表 | 真实 benchmark | 2022 |
| JOB 29a/b/c | 17 张表 | 真实 IMDB 数据 | 2015 |
| Synthetic (SynQL) | 任意规模 | 可生成但缺乏真实性 | 2024 |

---

### 5.3 规模分级

**小规模 (≤8 表)**:
- TPC-H 覆盖范围
- 精确算法 (DPhyp) 可行
- 优化时间可接受

**中等规模 (9-12 表)**:
- JOB 大部分查询
- 精确算法开始吃力
- 启发式算法开始有价值

**大规模 (13-25 表)**:
- JOB 最复杂查询 (17 表)
- AdaptiveQO、MPDP 测试范围
- 需要自适应或并行算法

**超大规模 (25+ 表)**:
- 主要是理论研究
- 缺乏公开的真实 benchmark
- 依赖 synthetic 生成器

**极大规模 (数百/数千表)**:
- AdaptiveQO 声称可扩展
- 缺乏实际验证
- 实际应用场景罕见

---

## 6. 关键发现

### 6.1 真实 Benchmark 的局限

**JOB 的局限**:
- 最大 17 张表，对于研究超大规模 join 不够
- 但提供了真实数据和真实相关性
- 已成为事实标准，便于对比

**TPC-DS 的局限**:
- 缺乏具体的 join 表数统计
- 更关注整体 OLAP 性能而非 join order
- 查询复杂度高但不专注于 join

**TPC-H 的局限**:
- 表数太少 (最大 8 表)
- 不适合作为 join-order 主验收集
- 只适合 smoke test

---

### 6.2 Synthetic vs 真实

**Synthetic 的优势**:
- 可生成任意规模的查询
- 可控的复杂度和拓扑
- 不受真实数据集限制

**Synthetic 的劣势**:
- 缺乏真实数据的相关性
- 难以反映生产环境的复杂性
- Cardinality 估计错误模式不真实

**真实的优势**:
- 真实数据相关性
- 真实的 cardinality 估计挑战
- 权威性和可对比性

**真实的劣势**:
- 规模受限于数据集
- 难以覆盖所有场景
- 获取和维护成本高

---

### 6.3 研究趋势

**算法研究**:
- 12 表以下：精确算法 (DPhyp) 主导
- 12-25 表：自适应算法 (AdaptiveQO, MPDP)
- 25+ 表：理论研究，缺乏公开 benchmark

**学习型优化器**:
- 主要在 JOB (≤17 表) 上验证
- 关注 workload 整体性能而非单查询
- 强调 P95/P99 tail latency

**Benchmark 发展**:
- JOB (2015) → JOB-Complex (2025)
- 从规模扩展转向真实复杂度
- 关注生产环境的实际挑战

---

## 7. 参考文献

### 7.1 Benchmark 论文

1. **JOB**: Viktor Leis, et al. "How Good Are Query Optimizers, Really?" VLDB 2015
   - [ResearchGate](https://www.researchgate.net/publication/305253105_How_Good_Are_Query_Optimizers_Really)

2. **JOB-Complex**: "A Challenging Benchmark for Traditional & Learned Query Optimization" AIDB@VLDB'25
   - [ArXiv](https://arxiv.org/abs/2507.07471)

3. **TPC-DS**: [Official Website](https://www.tpc.org/tpcds/)

4. **LDBC SNB**: [Official Website](https://ldbcouncil.org/benchmarks/snb/)

---

### 7.2 大规模 Join 研究

1. **AdaptiveQO**: Thomas Neumann, et al. "Adaptive Optimization of Very Large Join Queries" SIGMOD 2018
   - [ACM DL](https://dl.acm.org/doi/10.1145/3183713.3183733)

2. **MPDP**: "Efficient Massively Parallel Join Optimization for Large Queries" 2022
   - [ArXiv](https://arxiv.org/abs/2202.13511)

3. **Join Ordering Myth**: "Debunking the Myth of Join Ordering" 2025
   - [ArXiv](https://arxiv.org/html/2502.15181v2)

---

### 7.3 Synthetic Benchmark

1. **SynQL**: "A Controllable and Scalable Rule-Based Framework for SQL Workload Synthesis" 2024
   - [ArXiv](https://arxiv.org/abs/2604.08021)

---

### 7.4 学习型优化器

1. **Neo**: Ryan Marcus, et al. "Neo: A Learned Query Optimizer" VLDB 2019
   - [ArXiv](https://arxiv.org/abs/1904.03711)

2. **Bao**: Ryan Marcus, et al. "Bao: Learning to Steer Query Optimizers" SIGMOD 2021
   - [ACM DL](https://dl.acm.org/doi/10.1145/3448016.3452838)

3. **Lero**: Xuanhe Yu, et al. "Lero: applying learning-to-rank in query optimizer" VLDB 2023
   - [Springer](https://link.springer.com/article/10.1007/s00778-024-00850-3)

4. **Balsa**: "Learning a Query Optimizer Without Expert Demonstrations" SIGMOD 2022
   - [ArXiv](https://arxiv.org/abs/2201.01441)

5. **NeuSO**: "Neural Optimizer for Subgraph Queries" SIGMOD 2026
   - [ArXiv](https://arxiv.org/abs/2509.23775)

---

## 附录：JOB 查询规模详细分布

### A.1 按表数分组

**4 张表 (3 个查询)**:
- 03a, 03b, 03c

**5 张表 (20 个查询)**:
- 01a/b/c/d, 02a/b/c/d, 04a/b/c, 05a/b/c, 06a/b/c/d/e/f

**6 张表 (2 个查询)**:
- 32a, 32b

**7 张表 (16 个查询)**:
- 08a/b/c/d, 10a/b/c, 17a/b/c/d/e/f, 18a/b/c

**8 张表 (21 个查询)**:
- 07a/b/c, 09a/b/c/d, 11a/b/c/d, 12a/b/c, 14a/b/c, 16a/b/c/d

**9 张表 (14 个查询)**:
- 13a/b/c/d, 15a/b/c/d, 21a/b/c, 25a/b/c

**10 张表 (7 个查询)**:
- 19a/b/c/d, 20a/b/c

**11 张表 (10 个查询)**:
- 22a/b/c/d, 23a/b/c, 31a/b/c

**12 张表 (11 个查询)**:
- 24a/b, 26a/b/c, 27a/b/c, 30a/b/c

**14 张表 (6 个查询)**:
- 28a/b/c, 33a/b/c

**17 张表 (3 个查询)**:
- 29a/b/c

---

**文档版本**: 1.0
**最后更新**: 2026-05-12
**调研人员**: Claude (Anthropic)
