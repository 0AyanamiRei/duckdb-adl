# Generated: TPC-H Schema Notes

English TL;DR: Placeholder for generated TPC-H schema and join-key metadata used by ADL-OPT experiments.

Updated: 2026-05-06

Key terms: generated, TPC-H, schema, join key, DuckDB

## Status

Placeholder. A future runner should generate this from DuckDB or from `extension/tpch/dbgen/dbgen.cpp`.

## Tables

TPC-H tables expected by the v0 harness:

- `region`
- `nation`
- `supplier`
- `customer`
- `part`
- `partsupp`
- `orders`
- `lineitem`

## Common Join Keys

- `customer.c_custkey = orders.o_custkey`
- `orders.o_orderkey = lineitem.l_orderkey`
- `lineitem.l_partkey = part.p_partkey`
- `lineitem.l_suppkey = supplier.s_suppkey`
- `partsupp.ps_partkey = part.p_partkey`
- `partsupp.ps_suppkey = supplier.s_suppkey`
- `supplier.s_nationkey = nation.n_nationkey`
- `customer.c_nationkey = nation.n_nationkey`
- `nation.n_regionkey = region.r_regionkey`

## Initial Query Set

- Q3
- Q5
- Q8
- Q9
- Q10
