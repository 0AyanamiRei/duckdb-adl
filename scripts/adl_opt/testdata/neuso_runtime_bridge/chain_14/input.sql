-- NeuSO runtime bridge regression case: 14-way regular inner chain join.
-- This is the smallest JOB-like large join smoke that proves the runtime
-- sidecar can return an order that DuckDB then uses to construct a join plan.

CREATE OR REPLACE TABLE t0 AS SELECT i::INTEGER AS i FROM range(100) AS r(i);
CREATE OR REPLACE TABLE t1 AS SELECT i::INTEGER AS i FROM range(100) AS r(i);
CREATE OR REPLACE TABLE t2 AS SELECT i::INTEGER AS i FROM range(100) AS r(i);
CREATE OR REPLACE TABLE t3 AS SELECT i::INTEGER AS i FROM range(100) AS r(i);
CREATE OR REPLACE TABLE t4 AS SELECT i::INTEGER AS i FROM range(100) AS r(i);
CREATE OR REPLACE TABLE t5 AS SELECT i::INTEGER AS i FROM range(100) AS r(i);
CREATE OR REPLACE TABLE t6 AS SELECT i::INTEGER AS i FROM range(100) AS r(i);
CREATE OR REPLACE TABLE t7 AS SELECT i::INTEGER AS i FROM range(100) AS r(i);
CREATE OR REPLACE TABLE t8 AS SELECT i::INTEGER AS i FROM range(100) AS r(i);
CREATE OR REPLACE TABLE t9 AS SELECT i::INTEGER AS i FROM range(100) AS r(i);
CREATE OR REPLACE TABLE t10 AS SELECT i::INTEGER AS i FROM range(100) AS r(i);
CREATE OR REPLACE TABLE t11 AS SELECT i::INTEGER AS i FROM range(100) AS r(i);
CREATE OR REPLACE TABLE t12 AS SELECT i::INTEGER AS i FROM range(100) AS r(i);
CREATE OR REPLACE TABLE t13 AS SELECT i::INTEGER AS i FROM range(100) AS r(i);

SELECT count(*)
FROM t0
JOIN t1 ON t0.i = t1.i
JOIN t2 ON t1.i = t2.i
JOIN t3 ON t2.i = t3.i
JOIN t4 ON t3.i = t4.i
JOIN t5 ON t4.i = t5.i
JOIN t6 ON t5.i = t6.i
JOIN t7 ON t6.i = t7.i
JOIN t8 ON t7.i = t8.i
JOIN t9 ON t8.i = t9.i
JOIN t10 ON t9.i = t10.i
JOIN t11 ON t10.i = t11.i
JOIN t12 ON t11.i = t12.i
JOIN t13 ON t12.i = t13.i;
