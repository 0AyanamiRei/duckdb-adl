#!/usr/bin/env python3
"""ADL-OPT v0 offline TPC-H harness.

This script intentionally stays outside DuckDB's C++ optimizer. It can generate
static query graph/state/transition/decision JSONL artifacts without a DuckDB
binary, and it can optionally execute baseline SQL variants when a DuckDB CLI is
provided.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


UPDATED = "2026-05-06"


@dataclass(frozen=True)
class Alias:
    alias: str
    table: str


@dataclass(frozen=True)
class Edge:
    left: str
    right: str
    predicate: str


@dataclass(frozen=True)
class QuerySpec:
    query_id: str
    sql_file: str
    aliases: tuple[Alias, ...]
    edges: tuple[Edge, ...]
    filters: tuple[str, ...]


QUERY_SPECS: dict[str, QuerySpec] = {
    "q03": QuerySpec(
        query_id="tpch_q03",
        sql_file="extension/tpch/dbgen/queries/q03.sql",
        aliases=(
            Alias("c", "customer"),
            Alias("o", "orders"),
            Alias("l", "lineitem"),
        ),
        edges=(
            Edge("c", "o", "c_custkey = o_custkey"),
            Edge("o", "l", "l_orderkey = o_orderkey"),
        ),
        filters=(
            "c_mktsegment = 'BUILDING'",
            "o_orderdate < CAST('1995-03-15' AS date)",
            "l_shipdate > CAST('1995-03-15' AS date)",
        ),
    ),
    "q05": QuerySpec(
        query_id="tpch_q05",
        sql_file="extension/tpch/dbgen/queries/q05.sql",
        aliases=(
            Alias("c", "customer"),
            Alias("o", "orders"),
            Alias("l", "lineitem"),
            Alias("s", "supplier"),
            Alias("n", "nation"),
            Alias("r", "region"),
        ),
        edges=(
            Edge("c", "o", "c_custkey = o_custkey"),
            Edge("o", "l", "l_orderkey = o_orderkey"),
            Edge("l", "s", "l_suppkey = s_suppkey"),
            Edge("c", "s", "c_nationkey = s_nationkey"),
            Edge("s", "n", "s_nationkey = n_nationkey"),
            Edge("n", "r", "n_regionkey = r_regionkey"),
        ),
        filters=(
            "r_name = 'ASIA'",
            "o_orderdate >= CAST('1994-01-01' AS date)",
            "o_orderdate < CAST('1995-01-01' AS date)",
        ),
    ),
    "q08": QuerySpec(
        query_id="tpch_q08",
        sql_file="extension/tpch/dbgen/queries/q08.sql",
        aliases=(
            Alias("p", "part"),
            Alias("s", "supplier"),
            Alias("l", "lineitem"),
            Alias("o", "orders"),
            Alias("c", "customer"),
            Alias("n1", "nation"),
            Alias("n2", "nation"),
            Alias("r", "region"),
        ),
        edges=(
            Edge("p", "l", "p_partkey = l_partkey"),
            Edge("s", "l", "s_suppkey = l_suppkey"),
            Edge("l", "o", "l_orderkey = o_orderkey"),
            Edge("o", "c", "o_custkey = c_custkey"),
            Edge("c", "n1", "c_nationkey = n1.n_nationkey"),
            Edge("n1", "r", "n1.n_regionkey = r_regionkey"),
            Edge("s", "n2", "s_nationkey = n2.n_nationkey"),
        ),
        filters=(
            "r_name = 'AMERICA'",
            "o_orderdate BETWEEN CAST('1995-01-01' AS date) AND CAST('1996-12-31' AS date)",
            "p_type = 'ECONOMY ANODIZED STEEL'",
        ),
    ),
    "q09": QuerySpec(
        query_id="tpch_q09",
        sql_file="extension/tpch/dbgen/queries/q09.sql",
        aliases=(
            Alias("p", "part"),
            Alias("s", "supplier"),
            Alias("l", "lineitem"),
            Alias("ps", "partsupp"),
            Alias("o", "orders"),
            Alias("n", "nation"),
        ),
        edges=(
            Edge("s", "l", "s_suppkey = l_suppkey"),
            Edge("ps", "l", "ps_suppkey = l_suppkey"),
            Edge("ps", "l", "ps_partkey = l_partkey"),
            Edge("p", "l", "p_partkey = l_partkey"),
            Edge("o", "l", "o_orderkey = l_orderkey"),
            Edge("s", "n", "s_nationkey = n_nationkey"),
        ),
        filters=("p_name LIKE '%green%'",),
    ),
    "q10": QuerySpec(
        query_id="tpch_q10",
        sql_file="extension/tpch/dbgen/queries/q10.sql",
        aliases=(
            Alias("c", "customer"),
            Alias("o", "orders"),
            Alias("l", "lineitem"),
            Alias("n", "nation"),
        ),
        edges=(
            Edge("c", "o", "c_custkey = o_custkey"),
            Edge("o", "l", "l_orderkey = o_orderkey"),
            Edge("c", "n", "c_nationkey = n_nationkey"),
        ),
        filters=(
            "o_orderdate >= CAST('1993-10-01' AS date)",
            "o_orderdate < CAST('1994-01-01' AS date)",
            "l_returnflag = 'R'",
        ),
    ),
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_sql(repo: Path, spec: QuerySpec) -> str:
    return (repo / spec.sql_file).read_text()


def table_ref(alias: Alias) -> str:
    if alias.alias == alias.table:
        return alias.table
    return f"{alias.table} {alias.alias}"


def state_id(aliases: Iterable[str]) -> str:
    return "+".join(sorted(aliases))


def edge_touches(edge: Edge, aliases: set[str]) -> bool:
    return edge.left in aliases and edge.right in aliases


def is_connected(aliases: set[str], edges: Iterable[Edge]) -> bool:
    if not aliases:
        return True
    start = next(iter(aliases))
    seen = {start}
    changed = True
    while changed:
        changed = False
        for edge in edges:
            if edge.left in seen and edge.right in aliases and edge.right not in seen:
                seen.add(edge.right)
                changed = True
            if edge.right in seen and edge.left in aliases and edge.left not in seen:
                seen.add(edge.left)
                changed = True
    return seen == aliases


def connected_states(spec: QuerySpec) -> list[tuple[str, ...]]:
    aliases = [a.alias for a in spec.aliases]
    states: list[tuple[str, ...]] = []
    for size in range(1, len(aliases) + 1):
        for combo in itertools.combinations(aliases, size):
            if is_connected(set(combo), spec.edges):
                states.append(tuple(sorted(combo)))
    return states


def valid_additions(spec: QuerySpec, current: set[str]) -> list[str]:
    aliases = {a.alias for a in spec.aliases}
    if not current:
        return sorted(aliases)
    additions = []
    for alias in aliases - current:
        if is_connected(current | {alias}, spec.edges):
            additions.append(alias)
    return sorted(additions)


def transitions(spec: QuerySpec) -> list[tuple[tuple[str, ...], tuple[str, ...], str, list[str]]]:
    result = []
    for state in [tuple()] + connected_states(spec):
        current = set(state)
        if len(current) == len(spec.aliases):
            continue
        for added in valid_additions(spec, current):
            target = tuple(sorted(current | {added}))
            predicates = [
                edge.predicate
                for edge in spec.edges
                if added in {edge.left, edge.right} and edge_touches(edge, current | {added})
            ]
            result.append((tuple(sorted(state)), target, added, predicates))
    return result


def original_order(spec: QuerySpec) -> list[str]:
    return [a.alias for a in spec.aliases]


def cardinality_heuristic_order(spec: QuerySpec) -> list[str]:
    degree = {a.alias: 0 for a in spec.aliases}
    for edge in spec.edges:
        degree[edge.left] += 1
        degree[edge.right] += 1
    current: set[str] = set()
    path: list[str] = []
    while len(path) < len(spec.aliases):
        candidates = valid_additions(spec, current)
        chosen = sorted(candidates, key=lambda a: (-degree[a], a))[0]
        path.append(chosen)
        current.add(chosen)
    return path


def random_valid_order(spec: QuerySpec, rng: random.Random) -> list[str]:
    current: set[str] = set()
    path: list[str] = []
    while len(path) < len(spec.aliases):
        candidates = valid_additions(spec, current)
        chosen = rng.choice(candidates)
        path.append(chosen)
        current.add(chosen)
    return path


def join_predicates_for_addition(spec: QuerySpec, current: set[str], added: str) -> list[str]:
    predicates = []
    for edge in spec.edges:
        if edge.left == added and edge.right in current:
            predicates.append(edge.predicate)
        elif edge.right == added and edge.left in current:
            predicates.append(edge.predicate)
    return predicates


def build_join_from_path(spec: QuerySpec, path: list[str]) -> str:
    alias_map = {a.alias: a for a in spec.aliases}
    current = {path[0]}
    expr = table_ref(alias_map[path[0]])
    for added in path[1:]:
        predicates = join_predicates_for_addition(spec, current, added)
        if not predicates:
            raise ValueError(f"Invalid path for {spec.query_id}: cannot add {added} to {sorted(current)}")
        on_clause = " AND ".join(predicates)
        expr = f"({expr} JOIN {table_ref(alias_map[added])} ON {on_clause})"
        current.add(added)
    return expr


def alias_marker(alias: Alias) -> str:
    if alias.alias == alias.table:
        return alias.table
    return f"{alias.table} {alias.alias}"


def replace_from_clause(sql: str, spec: QuerySpec, join_expr: str) -> str:
    """Replace the target comma-style FROM list with an explicit join tree.

    Some TPC-H queries wrap the real join graph in a subquery. A simple non-greedy
    FROM..WHERE regex would match the outer FROM and corrupt the query. Instead,
    examine every FROM token, keep candidates that mention every expected alias,
    and choose the shortest candidate, which is the innermost target join block.
    """
    from_matches = list(re.finditer(r"\bFROM\b", sql, re.IGNORECASE))
    candidates: list[tuple[int, int, int]] = []
    lowered = sql.lower()
    for match in from_matches:
        where_match = re.search(r"\bWHERE\b", sql[match.end() :], re.IGNORECASE)
        if not where_match:
            continue
        start = match.end()
        end = match.end() + where_match.start()
        candidate = lowered[start:end]
        if all(re.search(rf"\b{re.escape(alias.table.lower())}\b", candidate) for alias in spec.aliases):
            candidates.append((end - start, start, end))
    if not candidates:
        raise ValueError(f"Could not find target FROM clause for {spec.query_id}")
    _, start, end = min(candidates)
    return sql[:start] + f"\n    {join_expr}\n" + sql[end:]


def variant_sql(repo: Path, spec: QuerySpec, path: list[str]) -> str:
    sql = read_sql(repo, spec)
    return replace_from_clause(sql, spec, build_join_from_path(spec, path))


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")


def duckdb_run(duckdb: Path, database: Path, sql: str, timeout: int) -> tuple[int, str, str, float]:
    cmd = [str(duckdb), str(database), "-csv", "-c", sql]
    start = time.perf_counter()
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    latency_ms = (time.perf_counter() - start) * 1000
    return proc.returncode, proc.stdout, proc.stderr, latency_ms


def fixed_order_session_sql(sql: str) -> str:
    return "SET disabled_optimizers='join_order';\n" + sql


def ensure_tpch_data(duckdb: Path, database: Path, scale_factor: float, timeout: int) -> None:
    sql = f"LOAD tpch; CALL dbgen(sf={scale_factor});"
    duckdb_run(duckdb, database, sql, timeout)


def result_checksum(csv_text: str) -> tuple[int, str]:
    lines = [line for line in csv_text.splitlines() if line]
    # DuckDB CSV output includes a header for SELECT results.
    row_count = max(0, len(lines) - 1)
    return row_count, sha256_text("\n".join(lines))


def explain_hash(duckdb: Path, database: Path, sql: str, timeout: int) -> tuple[str | None, bool, str | None]:
    code, out, err, _ = duckdb_run(duckdb, database, "EXPLAIN " + sql.rstrip().rstrip(";"), timeout)
    if code != 0:
        return None, False, err.strip() or "EXPLAIN failed"
    return sha256_text(out), True, None


def build_rows(repo: Path, specs: list[QuerySpec], scale_factor: float, seed: int, random_orders: int) -> dict[str, list[dict]]:
    rows = {name: [] for name in ["query_graph", "state", "transition", "decision", "run_result"]}
    for spec in specs:
        sql = read_sql(repo, spec)
        rows["query_graph"].append(
            {
                "query_id": spec.query_id,
                "workload": "tpch",
                "scale_factor": scale_factor,
                "aliases": [alias.__dict__ for alias in spec.aliases],
                "edges": [edge.__dict__ for edge in spec.edges],
                "filters": list(spec.filters),
                "sql_hash": sha256_text(sql),
                "updated": UPDATED,
            }
        )
        for state in connected_states(spec):
            rows["state"].append(
                {
                    "query_id": spec.query_id,
                    "state_id": state_id(state),
                    "aliases": list(state),
                    "is_connected": True,
                    "estimated_cardinality": None,
                    "actual_cardinality": None,
                    "feature_ref": None,
                    "source": "full" if len(spec.aliases) <= 6 else "partial",
                }
            )
        transition_ids = {}
        for source, target, added, predicates in transitions(spec):
            tid = f"{spec.query_id}:{state_id(source) or 'empty'}->{state_id(target)}"
            transition_ids[(source, added)] = tid
            rows["transition"].append(
                {
                    "query_id": spec.query_id,
                    "transition_id": tid,
                    "from_state_id": state_id(source),
                    "to_state_id": state_id(target),
                    "added_alias": added,
                    "valid": True,
                    "predicate_refs": predicates,
                    "estimated_cost": None,
                    "runtime_cost": None,
                    "label_source": "unknown",
                }
            )
        variants: list[tuple[str, str, list[str], int | None]] = [
            ("duckdb_default", "duckdb_default", [], None),
            ("sql_original", "sql_original", original_order(spec), None),
            ("cardinality_heuristic", "cardinality_heuristic", cardinality_heuristic_order(spec), None),
        ]
        rng = random.Random(seed)
        for idx in range(random_orders):
            variants.append((f"random_valid_{idx}", "random_valid", random_valid_order(spec, rng), seed + idx))
        for variant_id, baseline_kind, path, variant_seed in variants:
            rows["run_result"].append(
                {
                    "query_id": spec.query_id,
                    "variant_id": f"{spec.query_id}:{variant_id}",
                    "baseline_kind": baseline_kind,
                    "join_path": path,
                    "sql_hash": sha256_text(
                        sql if baseline_kind in {"duckdb_default", "sql_original"} else variant_sql(repo, spec, path)
                    ),
                    "explain_hash": None,
                    "plan_control_valid": baseline_kind == "duckdb_default",
                    "correct": False,
                    "row_count": None,
                    "result_checksum": None,
                    "latency_ms": None,
                    "optimizer_time_ms": None,
                    "execution_time_ms": None,
                    "timeout": False,
                    "failure_reason": "not_executed",
                }
            )
            if baseline_kind in {"duckdb_default", "sql_original"}:
                continue
            current: set[str] = set()
            for step, alias in enumerate(path):
                candidates = valid_additions(spec, current)
                rows["decision"].append(
                    {
                        "query_id": spec.query_id,
                        "decision_id": f"{spec.query_id}:{variant_id}:{step}",
                        "policy": baseline_kind,
                        "from_state_id": state_id(current),
                        "chosen_transition_id": transition_ids.get((tuple(sorted(current)), alias)),
                        "candidate_transition_ids": [
                            transition_ids.get((tuple(sorted(current)), candidate)) for candidate in candidates
                        ],
                        "score": None,
                        "valid": alias in candidates,
                        "seed": variant_seed,
                    }
                )
                current.add(alias)
    return rows


def execute_rows(repo: Path, rows: dict[str, list[dict]], specs: list[QuerySpec], args: argparse.Namespace) -> None:
    if not args.duckdb:
        return
    duckdb = Path(args.duckdb)
    database = Path(args.database)
    ensure_tpch_data(duckdb, database, args.scale_factor, args.timeout)
    spec_by_id = {spec.query_id: spec for spec in specs}
    default_results: dict[str, tuple[int, str]] = {}
    for row in rows["run_result"]:
        spec = spec_by_id[row["query_id"]]
        if row["baseline_kind"] in {"duckdb_default", "sql_original"}:
            sql = read_sql(repo, spec)
        else:
            sql = fixed_order_session_sql(variant_sql(repo, spec, row["join_path"]))
        try:
            explain, plan_valid, explain_error = explain_hash(duckdb, database, sql, args.timeout)
            code, out, err, latency_ms = duckdb_run(duckdb, database, sql, args.timeout)
        except subprocess.TimeoutExpired:
            row.update({"timeout": True, "failure_reason": "timeout"})
            continue
        row["explain_hash"] = explain
        row["plan_control_valid"] = row["baseline_kind"] == "duckdb_default" or plan_valid
        row["latency_ms"] = latency_ms
        if code != 0:
            row["failure_reason"] = err.strip() or explain_error or "execution failed"
            continue
        checksum = result_checksum(out)
        row["row_count"], row["result_checksum"] = checksum
        if row["baseline_kind"] == "duckdb_default":
            default_results[row["query_id"]] = checksum
            row["correct"] = True
            row["failure_reason"] = None
        else:
            row["correct"] = default_results.get(row["query_id"]) == checksum
            row["failure_reason"] = None if row["correct"] else "checksum_mismatch"


def write_summary(output: Path, rows: dict[str, list[dict]], executed: bool) -> None:
    run_rows = rows["run_result"]
    summary = {
        "updated": UPDATED,
        "executed": executed,
        "query_count": len({row["query_id"] for row in rows["query_graph"]}),
        "variant_count": len(run_rows),
        "correctness_failures": sum(1 for row in run_rows if row["failure_reason"] not in (None, "not_executed")),
        "plan_control_failures": sum(1 for row in run_rows if not row["plan_control_valid"]),
        "timeout_count": sum(1 for row in run_rows if row["timeout"]),
        "jsonl_files": [f"{name}.jsonl" for name in ["query_graph", "state", "transition", "run_result", "decision"]],
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    lines = [
        "# ADL-OPT Run Summary",
        "",
        f"English TL;DR: Generated ADL-OPT v0 artifacts for {summary['query_count']} TPC-H queries.",
        "",
        f"Updated: {UPDATED}",
        "",
        "Key terms: ADL-OPT, TPC-H, JSONL, run summary",
        "",
        f"- Executed DuckDB: {executed}",
        f"- Query count: {summary['query_count']}",
        f"- Variant count: {summary['variant_count']}",
        f"- Correctness failures: {summary['correctness_failures']}",
        f"- Plan-control failures: {summary['plan_control_failures']}",
        f"- Timeout count: {summary['timeout_count']}",
    ]
    (output / "summary.md").write_text("\n".join(lines) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate ADL-OPT v0 TPC-H JSONL artifacts")
    parser.add_argument("--repo", default=".", help="DuckDB repository root")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--queries", nargs="+", default=["q03", "q05", "q08"], choices=sorted(QUERY_SPECS))
    parser.add_argument("--scale-factor", type=float, default=0.1)
    parser.add_argument("--random-orders", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260506)
    parser.add_argument("--duckdb", help="Optional DuckDB CLI/binary path")
    parser.add_argument("--database", default="/tmp/adl-opt-tpch.duckdb")
    parser.add_argument("--execute", action="store_true", help="Execute variants using --duckdb")
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo = Path(args.repo).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    specs = [QUERY_SPECS[name] for name in args.queries]
    rows = build_rows(repo, specs, args.scale_factor, args.seed, args.random_orders)
    if args.execute:
        execute_rows(repo, rows, specs, args)
    for name in ["query_graph", "state", "transition", "run_result", "decision"]:
        write_jsonl(output / f"{name}.jsonl", rows[name])
    write_summary(output, rows, args.execute and bool(args.duckdb))


if __name__ == "__main__":
    main()
