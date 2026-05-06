#!/usr/bin/env python3
"""ADL-OPT offline large-join harness for JOB/IMDB queries.

This script is intentionally static: it does not change DuckDB's optimizer and
does not require a DuckDB binary. It parses selected JOB/IMDB SQL files, emits a
large-join query graph, creates a fixture linear order, and materializes
NeuSO-style endpoint-append decisions over that linear order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


UPDATED = "2026-05-06"
DEFAULT_QUERIES = (
    "29a",
    "29b",
    "29c",
    "28a",
    "28b",
    "28c",
    "33a",
    "33b",
    "33c",
)


@dataclass(frozen=True)
class Alias:
    relation_id: int
    alias: str
    table: str


@dataclass(frozen=True)
class Edge:
    edge_id: str
    left: str
    right: str
    predicate: str
    left_column: str
    right_column: str


@dataclass(frozen=True)
class QuerySpec:
    query_id: str
    workload_query: str
    sql_file: str
    aliases: tuple[Alias, ...]
    edges: tuple[Edge, ...]
    filters: tuple[str, ...]
    sql_hash: str


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")


def split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    in_quote = False
    idx = 0
    while idx < len(text):
        ch = text[idx]
        if ch == "'":
            in_quote = not in_quote
        elif not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(text[start:idx].strip())
                start = idx + 1
        idx += 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def split_top_level_and(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    in_quote = False
    between_pending = False
    idx = 0
    while idx < len(text):
        ch = text[idx]
        if ch == "'":
            in_quote = not in_quote
            idx += 1
            continue
        if in_quote:
            idx += 1
            continue
        if ch == "(":
            depth += 1
            idx += 1
            continue
        if ch == ")":
            depth -= 1
            idx += 1
            continue
        if depth == 0 and text[idx : idx + 7].upper() == "BETWEEN":
            before_ok = idx == 0 or not text[idx - 1].isalnum()
            after_ok = idx + 7 == len(text) or not text[idx + 7].isalnum()
            if before_ok and after_ok:
                between_pending = True
                idx += 7
                continue
        if depth == 0 and text[idx : idx + 3].upper() == "AND":
            before_ok = idx == 0 or not text[idx - 1].isalnum()
            after_ok = idx + 3 == len(text) or not text[idx + 3].isalnum()
            if before_ok and after_ok:
                if between_pending:
                    between_pending = False
                    idx += 3
                    continue
                part = text[start:idx].strip()
                if part:
                    parts.append(part)
                start = idx + 3
                idx += 3
                continue
        idx += 1
    tail = text[start:].strip().rstrip(";").strip()
    if tail:
        parts.append(tail)
    return parts


def parse_from_aliases(sql: str) -> tuple[Alias, ...]:
    match = re.search(r"\bFROM\b(.*?)\bWHERE\b", sql, re.IGNORECASE | re.DOTALL)
    if not match:
        raise ValueError("SQL does not contain a simple FROM ... WHERE block")
    aliases: list[Alias] = []
    for relation_id, item in enumerate(split_top_level_commas(match.group(1))):
        tokens = item.split()
        if len(tokens) >= 3 and tokens[1].upper() == "AS":
            table, alias = tokens[0], tokens[2]
        elif len(tokens) >= 2:
            table, alias = tokens[0], tokens[1]
        elif len(tokens) == 1:
            table = alias = tokens[0]
        else:
            raise ValueError(f"Could not parse FROM item: {item!r}")
        aliases.append(Alias(relation_id=relation_id, alias=alias, table=table))
    return tuple(aliases)


def parse_where_predicates(sql: str) -> list[str]:
    match = re.search(r"\bWHERE\b(.*?);?\s*$", sql, re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    return split_top_level_and(match.group(1))


JOIN_EQ_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z_0-9]*)\.([A-Za-z_][A-Za-z_0-9]*)\s*=\s*"
    r"([A-Za-z_][A-Za-z_0-9]*)\.([A-Za-z_][A-Za-z_0-9]*)\b"
)


def parse_query(repo: Path, workload_query: str) -> QuerySpec:
    sql_file = Path("benchmark/imdb_plan_cost/queries") / f"{workload_query}.sql"
    sql = (repo / sql_file).read_text()
    aliases = parse_from_aliases(sql)
    alias_names = {alias.alias for alias in aliases}
    filters: list[str] = []
    edges: list[Edge] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for predicate in parse_where_predicates(sql):
        matches = list(JOIN_EQ_RE.finditer(predicate))
        edge_found = False
        for match in matches:
            left_alias, left_column, right_alias, right_column = match.groups()
            if left_alias not in alias_names or right_alias not in alias_names:
                continue
            if left_alias == right_alias:
                continue
            left, right = sorted((left_alias, right_alias))
            normalized_predicate = " ".join(match.group(0).split())
            key = (left, right, normalized_predicate)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edge_id = f"job_{workload_query}:edge:{len(edges)}"
            edges.append(
                Edge(
                    edge_id=edge_id,
                    left=left,
                    right=right,
                    predicate=normalized_predicate,
                    left_column=left_column if left == left_alias else right_column,
                    right_column=right_column if right == right_alias else left_column,
                )
            )
            edge_found = True
        if not edge_found:
            filters.append(" ".join(predicate.split()))
    return QuerySpec(
        query_id=f"job_{workload_query}",
        workload_query=workload_query,
        sql_file=sql_file.as_posix(),
        aliases=aliases,
        edges=tuple(edges),
        filters=tuple(filters),
        sql_hash=sha256_text(sql),
    )


def state_id(aliases: Iterable[str]) -> str:
    return "+".join(sorted(aliases))


def alias_degree(spec: QuerySpec) -> dict[str, int]:
    degree = {alias.alias: 0 for alias in spec.aliases}
    for edge in spec.edges:
        degree[edge.left] += 1
        degree[edge.right] += 1
    return degree


def predicates_for_addition(spec: QuerySpec, current: set[str], added: str) -> list[str]:
    predicates = []
    for edge in spec.edges:
        if edge.left == added and edge.right in current:
            predicates.append(edge.predicate)
        elif edge.right == added and edge.left in current:
            predicates.append(edge.predicate)
    return predicates


def valid_additions(spec: QuerySpec, current: set[str]) -> list[str]:
    aliases = {alias.alias for alias in spec.aliases}
    if not current:
        return sorted(aliases)
    return sorted(
        alias
        for alias in aliases - current
        if predicates_for_addition(spec, current, alias)
    )


def greedy_connected_path(spec: QuerySpec) -> tuple[list[str], bool]:
    degree = alias_degree(spec)
    aliases = {alias.alias for alias in spec.aliases}
    start = sorted(aliases, key=lambda alias: (-degree[alias], alias))[0]
    current = {start}
    path = [start]
    while len(path) < len(aliases):
        candidates = valid_additions(spec, current)
        if not candidates:
            remaining = sorted(aliases - current, key=lambda alias: (-degree[alias], alias))
            path.extend(remaining)
            return path, False
        chosen = sorted(candidates, key=lambda alias: (-degree[alias], alias))[0]
        path.append(chosen)
        current.add(chosen)
    return path, True


def folded_linear_order(path: list[str]) -> tuple[list[str], str]:
    if not path:
        return [], ""
    folded: deque[str] = deque([path[0]])
    for idx, alias in enumerate(path[1:], start=1):
        if idx % 2:
            folded.appendleft(alias)
        else:
            folded.append(alias)
    return list(folded), path[0]


def interval_state_id(query_id: str, linear_order_id: str, left: int, right: int) -> str:
    return f"{query_id}:{linear_order_id}:i{left}-{right}"


def endpoint_transition_id(
    query_id: str, linear_order_id: str, left: int, right: int, side: str
) -> str:
    return f"{query_id}:{linear_order_id}:i{left}-{right}:{side}"


def endpoint_candidates(
    spec: QuerySpec, linear_order: list[str], left: int, right: int
) -> list[dict]:
    current = set(linear_order[left : right + 1])
    candidates = []
    for side, index in (("left", left - 1), ("right", right + 1)):
        if index < 0 or index >= len(linear_order):
            continue
        alias = linear_order[index]
        predicates = predicates_for_addition(spec, current, alias)
        candidates.append(
            {
                "side": side,
                "index": index,
                "alias": alias,
                "valid": bool(predicates),
                "predicate_refs": predicates,
            }
        )
    return candidates


def interval_connected(spec: QuerySpec, aliases: list[str]) -> bool:
    if not aliases:
        return True
    alias_set = set(aliases)
    seen = {aliases[0]}
    changed = True
    while changed:
        changed = False
        for edge in spec.edges:
            if edge.left in seen and edge.right in alias_set and edge.right not in seen:
                seen.add(edge.right)
                changed = True
            if edge.right in seen and edge.left in alias_set and edge.left not in seen:
                seen.add(edge.left)
                changed = True
    return seen == alias_set


def states_for_linear_order(
    spec: QuerySpec, linear_order_id: str, linear_order: list[str]
) -> list[dict]:
    rows = []
    for left in range(len(linear_order)):
        for right in range(left, len(linear_order)):
            aliases = linear_order[left : right + 1]
            rows.append(
                {
                    "query_id": spec.query_id,
                    "state_id": interval_state_id(spec.query_id, linear_order_id, left, right),
                    "linear_order_id": linear_order_id,
                    "interval": [left, right],
                    "aliases": aliases,
                    "is_connected": interval_connected(spec, aliases),
                    "estimated_cardinality": None,
                    "actual_cardinality": None,
                    "feature_ref": None,
                    "source": "linearized_interval_fixture",
                }
            )
    return rows


def transitions_for_linear_order(
    spec: QuerySpec, linear_order_id: str, linear_order: list[str]
) -> list[dict]:
    rows = []
    for left in range(len(linear_order)):
        for right in range(left, len(linear_order)):
            from_id = interval_state_id(spec.query_id, linear_order_id, left, right)
            current = set(linear_order[left : right + 1])
            for candidate in endpoint_candidates(spec, linear_order, left, right):
                next_left = candidate["index"] if candidate["side"] == "left" else left
                next_right = candidate["index"] if candidate["side"] == "right" else right
                rows.append(
                    {
                        "query_id": spec.query_id,
                        "transition_id": endpoint_transition_id(
                            spec.query_id, linear_order_id, left, right, candidate["side"]
                        ),
                        "linear_order_id": linear_order_id,
                        "from_state_id": from_id,
                        "to_state_id": interval_state_id(
                            spec.query_id, linear_order_id, next_left, next_right
                        ),
                        "interval": [left, right],
                        "side": candidate["side"],
                        "added_alias": candidate["alias"],
                        "valid": candidate["valid"],
                        "predicate_refs": candidate["predicate_refs"],
                        "estimated_cost": None,
                        "runtime_cost": None,
                        "label_source": "unknown",
                        "transition_kind": "endpoint_append",
                        "current_aliases": sorted(current),
                    }
                )
    return rows


def follow_connected_path_decisions(
    spec: QuerySpec,
    linear_order_id: str,
    linear_order: list[str],
    connected_path: list[str],
) -> tuple[dict, list[dict]]:
    index_by_alias = {alias: idx for idx, alias in enumerate(linear_order)}
    start = connected_path[0]
    left = right = index_by_alias[start]
    decisions = []
    path = [start]
    sides = []
    valid = True
    failure_reason = None
    for step, expected_alias in enumerate(connected_path[1:], start=1):
        candidates = endpoint_candidates(spec, linear_order, left, right)
        candidate_ids = [
            endpoint_transition_id(spec.query_id, linear_order_id, left, right, item["side"])
            for item in candidates
        ]
        chosen = next((item for item in candidates if item["alias"] == expected_alias), None)
        if chosen is None:
            valid = False
            failure_reason = f"expected alias {expected_alias} was not an endpoint"
            break
        transition_id = endpoint_transition_id(
            spec.query_id, linear_order_id, left, right, chosen["side"]
        )
        decisions.append(
            {
                "query_id": spec.query_id,
                "decision_id": f"{spec.query_id}:adl_opt_endpoint_fixture:{step}",
                "policy": "adl_opt_endpoint_fixture",
                "linear_order_id": linear_order_id,
                "from_state_id": interval_state_id(spec.query_id, linear_order_id, left, right),
                "interval": [left, right],
                "chosen_transition_id": transition_id,
                "candidate_transition_ids": candidate_ids,
                "candidate_aliases": [item["alias"] for item in candidates],
                "chosen_alias": chosen["alias"],
                "side": chosen["side"],
                "score": None,
                "valid": chosen["valid"],
                "seed": None,
            }
        )
        if not chosen["valid"]:
            valid = False
            failure_reason = f"chosen alias {chosen['alias']} has no join edge to current state"
            break
        if chosen["side"] == "left":
            left -= 1
        else:
            right += 1
        path.append(chosen["alias"])
        sides.append(chosen["side"])
    return (
        {
            "query_id": spec.query_id,
            "path_id": f"{spec.query_id}:adl_opt_endpoint_fixture",
            "policy": "adl_opt_endpoint_fixture",
            "linear_order_id": linear_order_id,
            "start_alias": start,
            "join_path": path,
            "endpoint_sides": sides,
            "valid": valid and len(path) == len(linear_order),
            "failure_reason": failure_reason,
            "covered_alias_count": len(path),
        },
        decisions,
    )


def random_endpoint_path(
    spec: QuerySpec,
    linear_order_id: str,
    linear_order: list[str],
    rng: random.Random,
    path_index: int,
    seed: int,
) -> tuple[dict, list[dict]]:
    start_index = rng.randrange(len(linear_order))
    left = right = start_index
    start = linear_order[start_index]
    path = [start]
    sides = []
    decisions = []
    valid = True
    failure_reason = None
    step = 0
    while len(path) < len(linear_order):
        candidates = endpoint_candidates(spec, linear_order, left, right)
        valid_candidates = [item for item in candidates if item["valid"]]
        candidate_ids = [
            endpoint_transition_id(spec.query_id, linear_order_id, left, right, item["side"])
            for item in candidates
        ]
        if not valid_candidates:
            valid = False
            failure_reason = "no valid endpoint candidate"
            break
        chosen = rng.choice(valid_candidates)
        transition_id = endpoint_transition_id(
            spec.query_id, linear_order_id, left, right, chosen["side"]
        )
        decisions.append(
            {
                "query_id": spec.query_id,
                "decision_id": f"{spec.query_id}:random_endpoint_{path_index}:{step}",
                "policy": "random_endpoint",
                "linear_order_id": linear_order_id,
                "from_state_id": interval_state_id(spec.query_id, linear_order_id, left, right),
                "interval": [left, right],
                "chosen_transition_id": transition_id,
                "candidate_transition_ids": candidate_ids,
                "candidate_aliases": [item["alias"] for item in candidates],
                "chosen_alias": chosen["alias"],
                "side": chosen["side"],
                "score": None,
                "valid": True,
                "seed": seed,
            }
        )
        if chosen["side"] == "left":
            left -= 1
        else:
            right += 1
        path.append(chosen["alias"])
        sides.append(chosen["side"])
        step += 1
    return (
        {
            "query_id": spec.query_id,
            "path_id": f"{spec.query_id}:random_endpoint_{path_index}",
            "policy": "random_endpoint",
            "linear_order_id": linear_order_id,
            "start_alias": start,
            "join_path": path,
            "endpoint_sides": sides,
            "valid": valid and len(path) == len(linear_order),
            "failure_reason": failure_reason,
            "covered_alias_count": len(path),
            "seed": seed,
        },
        decisions,
    )


def build_rows(
    repo: Path, query_names: list[str], random_paths: int, seed: int, threshold: int
) -> dict[str, list[dict]]:
    rows = {
        name: []
        for name in [
            "query_graph",
            "linear_order",
            "state",
            "transition",
            "decision",
            "endpoint_path",
            "run_result",
        ]
    }
    for query_name in query_names:
        spec = parse_query(repo, query_name)
        relation_count = len(spec.aliases)
        rows["query_graph"].append(
            {
                "query_id": spec.query_id,
                "workload": "job_imdb",
                "workload_query": spec.workload_query,
                "scale_factor": None,
                "sql_file": spec.sql_file,
                "aliases": [alias.__dict__ for alias in spec.aliases],
                "edges": [edge.__dict__ for edge in spec.edges],
                "filters": list(spec.filters),
                "relation_count": relation_count,
                "join_edge_count": len(spec.edges),
                "large_join_threshold": threshold,
                "large_join_candidate": relation_count > threshold,
                "estimated_cardinality_available": False,
                "estimated_cost_available": False,
                "sql_hash": spec.sql_hash,
                "updated": UPDATED,
            }
        )
        connected_path, connected_path_valid = greedy_connected_path(spec)
        linear_order, start_alias = folded_linear_order(connected_path)
        linear_order_id = "fixture_folded_connected_degree"
        rows["linear_order"].append(
            {
                "query_id": spec.query_id,
                "linear_order_id": linear_order_id,
                "source": "fixture_folded_connected_degree",
                "true_linearization_algorithm": False,
                "linear_order": linear_order,
                "connected_seed_path": connected_path,
                "connected_seed_path_valid": connected_path_valid,
                "start_alias": start_alias,
                "relation_count": relation_count,
                "note": "Fixture only; Neumann-style linearization is tracked separately.",
                "updated": UPDATED,
            }
        )
        rows["state"].extend(states_for_linear_order(spec, linear_order_id, linear_order))
        rows["transition"].extend(
            transitions_for_linear_order(spec, linear_order_id, linear_order)
        )
        fixture_path, fixture_decisions = follow_connected_path_decisions(
            spec, linear_order_id, linear_order, connected_path
        )
        rows["endpoint_path"].append(fixture_path)
        rows["decision"].extend(fixture_decisions)
        rows["run_result"].extend(
            [
                {
                    "query_id": spec.query_id,
                    "variant_id": f"{spec.query_id}:duckdb_default",
                    "source_variant_id": None,
                    "baseline_kind": "duckdb_default",
                    "join_path": [],
                    "sql_hash": spec.sql_hash,
                    "explain_hash": None,
                    "plan_control_valid": False,
                    "correct": False,
                    "row_count": None,
                    "result_checksum": None,
                    "latency_ms": None,
                    "latency_p50_ms": None,
                    "latency_p95_ms": None,
                    "latency_samples_ms": [],
                    "optimizer_time_ms": None,
                    "execution_time_ms": None,
                    "speedup_vs_default": None,
                    "regret_vs_sampled_oracle": None,
                    "timeout": False,
                    "failure_reason": "not_executed",
                },
                {
                    "query_id": spec.query_id,
                    "variant_id": f"{spec.query_id}:sql_original",
                    "source_variant_id": None,
                    "baseline_kind": "sql_original",
                    "join_path": [alias.alias for alias in spec.aliases],
                    "sql_hash": spec.sql_hash,
                    "explain_hash": None,
                    "plan_control_valid": False,
                    "correct": False,
                    "row_count": None,
                    "result_checksum": None,
                    "latency_ms": None,
                    "latency_p50_ms": None,
                    "latency_p95_ms": None,
                    "latency_samples_ms": [],
                    "optimizer_time_ms": None,
                    "execution_time_ms": None,
                    "speedup_vs_default": None,
                    "regret_vs_sampled_oracle": None,
                    "timeout": False,
                    "failure_reason": "not_executed",
                },
                {
                    "query_id": spec.query_id,
                    "variant_id": fixture_path["path_id"],
                    "source_variant_id": None,
                    "baseline_kind": "adl_opt_endpoint_fixture",
                    "join_path": fixture_path["join_path"],
                    "sql_hash": spec.sql_hash,
                    "explain_hash": None,
                    "plan_control_valid": False,
                    "correct": False,
                    "row_count": None,
                    "result_checksum": None,
                    "latency_ms": None,
                    "latency_p50_ms": None,
                    "latency_p95_ms": None,
                    "latency_samples_ms": [],
                    "optimizer_time_ms": None,
                    "execution_time_ms": None,
                    "speedup_vs_default": None,
                    "regret_vs_sampled_oracle": None,
                    "timeout": False,
                    "failure_reason": "not_executed",
                },
            ]
        )
        for idx in range(random_paths):
            path_seed = seed + idx
            rng = random.Random(path_seed)
            endpoint_path, decisions = random_endpoint_path(
                spec, linear_order_id, linear_order, rng, idx, path_seed
            )
            rows["endpoint_path"].append(endpoint_path)
            rows["decision"].extend(decisions)
            rows["run_result"].append(
                {
                    "query_id": spec.query_id,
                    "variant_id": endpoint_path["path_id"],
                    "source_variant_id": None,
                    "baseline_kind": "random_endpoint",
                    "join_path": endpoint_path["join_path"],
                    "sql_hash": spec.sql_hash,
                    "explain_hash": None,
                    "plan_control_valid": False,
                    "correct": False,
                    "row_count": None,
                    "result_checksum": None,
                    "latency_ms": None,
                    "latency_p50_ms": None,
                    "latency_p95_ms": None,
                    "latency_samples_ms": [],
                    "optimizer_time_ms": None,
                    "execution_time_ms": None,
                    "speedup_vs_default": None,
                    "regret_vs_sampled_oracle": None,
                    "timeout": False,
                    "failure_reason": "not_executed",
                }
            )
    return rows


def write_summary(output: Path, rows: dict[str, list[dict]]) -> None:
    graphs = rows["query_graph"]
    paths = rows["endpoint_path"]
    per_query = {}
    for graph in graphs:
        query_id = graph["query_id"]
        query_paths = [path for path in paths if path["query_id"] == query_id]
        per_query[query_id] = {
            "relation_count": graph["relation_count"],
            "join_edge_count": graph["join_edge_count"],
            "large_join_candidate": graph["large_join_candidate"],
            "endpoint_path_count": len(query_paths),
            "valid_endpoint_path_count": sum(1 for path in query_paths if path["valid"]),
        }
    summary = {
        "updated": UPDATED,
        "executed": False,
        "workload": "job_imdb",
        "query_count": len(graphs),
        "min_relation_count": min((graph["relation_count"] for graph in graphs), default=0),
        "max_relation_count": max((graph["relation_count"] for graph in graphs), default=0),
        "large_join_candidate_count": sum(1 for graph in graphs if graph["large_join_candidate"]),
        "endpoint_path_count": len(paths),
        "valid_endpoint_path_count": sum(1 for path in paths if path["valid"]),
        "jsonl_files": [
            f"{name}.jsonl"
            for name in [
                "query_graph",
                "linear_order",
                "state",
                "transition",
                "decision",
                "endpoint_path",
                "run_result",
            ]
        ],
        "per_query": per_query,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    lines = [
        "# ADL-OPT Large-Join Static Summary",
        "",
        f"English TL;DR: Generated ADL-OPT n>12 large-join artifacts for {summary['query_count']} JOB/IMDB queries.",
        "",
        f"Updated: {UPDATED}",
        "",
        "Key terms: ADL-OPT, JOB/IMDB, large join, linear order, endpoint append",
        "",
        f"- Executed DuckDB: {summary['executed']}",
        f"- Query count: {summary['query_count']}",
        f"- Relation count range: {summary['min_relation_count']}..{summary['max_relation_count']}",
        f"- Large-join candidates: {summary['large_join_candidate_count']}",
        f"- Endpoint paths: {summary['endpoint_path_count']}",
        f"- Valid endpoint paths: {summary['valid_endpoint_path_count']}",
        "",
        "## Per Query",
        "",
    ]
    for query_id, stats in per_query.items():
        lines.append(
            f"- {query_id}: relations={stats['relation_count']}, "
            f"edges={stats['join_edge_count']}, "
            f"large_join={stats['large_join_candidate']}, "
            f"valid_paths={stats['valid_endpoint_path_count']}/{stats['endpoint_path_count']}"
        )
    (output / "summary.md").write_text("\n".join(lines) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate ADL-OPT n>12 JOB/IMDB large-join artifacts"
    )
    parser.add_argument("--repo", default=".", help="DuckDB repository root")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--queries", nargs="+", default=list(DEFAULT_QUERIES))
    parser.add_argument("--random-paths", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260506)
    parser.add_argument("--large-join-threshold", type=int, default=12)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo = Path(args.repo).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows = build_rows(
        repo=repo,
        query_names=args.queries,
        random_paths=args.random_paths,
        seed=args.seed,
        threshold=args.large_join_threshold,
    )
    for name in [
        "query_graph",
        "linear_order",
        "state",
        "transition",
        "decision",
        "endpoint_path",
        "run_result",
    ]:
        write_jsonl(output / f"{name}.jsonl", rows[name])
    write_summary(output, rows)


if __name__ == "__main__":
    main()
