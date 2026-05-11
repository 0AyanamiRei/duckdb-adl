#!/usr/bin/env python3
"""Executable ADL-OPT benchmark runner for classic JOB/IMDB queries.

The runner keeps the executable JOB benchmark separate from the static
large-join artifact generator. It measures two DuckDB-internal surfaces:

* plan latency: SQL -> logical/optimized/physical plan time from detailed profiling
* plan quality proxy: physical execution time from detailed profiling

The ADL-OPT applied variant sends a large regular join graph to the NeuSO
runtime sidecar and lets DuckDB construct the returned left-deep join order.
Valid random endpoint paths are applied through an explicit JOIN tree plus
disabled_optimizers='join_order'; invalid endpoint paths are kept as structured
skipped rows.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import random
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from offline_large_join_harness import (
    DEFAULT_QUERIES,
    QuerySpec,
    folded_linear_order,
    greedy_connected_path,
    parse_query,
    random_endpoint_path,
    sha256_text,
)


UPDATED = "2026-05-12"
WORKLOAD = "job_imdb"
DEFAULT_RUN_ID = "job_imdb_benchmark"
BASELINE_KINDS = {
    "duckdb_default",
    "sql_original",
    "ikkbz_top1_export",
    "adl_opt_applied",
    "neuso_runtime_validate",
    "random_endpoint",
}

PROFILE_PHASE_KEYS = {
    "parser_time_ms": ("parser",),
    "planner_time_ms": ("planner",),
    "planner_binding_time_ms": ("planner_binding",),
    "optimizer_time_ms": ("all_optimizers", "cumulative_optimizer_timing"),
    "join_order_optimizer_time_ms": ("optimizer_join_order",),
    "physical_planner_time_ms": ("physical_planner",),
    "physical_planner_create_plan_time_ms": ("physical_planner_create_plan",),
    "query_latency_ms": ("latency",),
    "execution_cpu_time_ms": ("cpu_time",),
}


@dataclass(frozen=True)
class VariantSpec:
    query_id: str
    variant_id: str
    baseline_kind: str
    executable: bool
    settings_kind: str
    join_path: list[str]
    endpoint_sides: list[str]
    seed: int | None
    note: str | None = None
    path_valid: bool | None = None
    path_failure_reason: str | None = None
    covered_alias_count: int | None = None


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * p
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def latency_stats(samples: list[float]) -> dict[str, float | None]:
    return {
        "p50_ms": percentile(samples, 0.50),
        "p95_ms": percentile(samples, 0.95),
        "p99_ms": percentile(samples, 0.99),
        "max_ms": max(samples) if samples else None,
    }


def geomean(values: list[float]) -> float | None:
    positive = [value for value in values if value and value > 0]
    if not positive:
        return None
    return math.exp(sum(math.log(value) for value in positive) / len(positive))


def read_sql(repo: Path, spec: QuerySpec) -> str:
    return (repo / spec.sql_file).read_text()


def sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def duckdb_run(
    duckdb: Path,
    database: Path,
    sql: str,
    timeout: int,
    *,
    csv_output: bool,
) -> tuple[int, str, str, float]:
    cmd = [str(duckdb), str(database)]
    if csv_output:
        cmd.append("-csv")
    cmd.extend(["-c", sql])
    start = time.perf_counter()
    proc = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    latency_ms = (time.perf_counter() - start) * 1000
    return proc.returncode, proc.stdout, proc.stderr, latency_ms


def result_checksum(csv_text: str) -> tuple[int, str]:
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        return 0, sha256_text("")
    data_rows = rows[1:]
    normalized_rows = sorted(
        json.dumps(row, ensure_ascii=True, separators=(",", ":")) for row in data_rows
    )
    return len(data_rows), sha256_text("\n".join(normalized_rows))


def table_ref(alias) -> str:
    if alias.alias == alias.table:
        return alias.table
    return f"{alias.table} AS {alias.alias}"


def join_predicates_for_addition(spec: QuerySpec, current: set[str], added: str) -> list[str]:
    predicates = []
    for edge in spec.edges:
        if edge.left == added and edge.right in current:
            predicates.append(edge.predicate)
        elif edge.right == added and edge.left in current:
            predicates.append(edge.predicate)
    return predicates


def build_join_from_path(spec: QuerySpec, path: list[str]) -> str:
    if not path:
        raise ValueError("empty join path")
    alias_map = {alias.alias: alias for alias in spec.aliases}
    current = {path[0]}
    expr = table_ref(alias_map[path[0]])
    for added in path[1:]:
        predicates = join_predicates_for_addition(spec, current, added)
        if not predicates:
            raise ValueError(
                f"invalid join path for {spec.query_id}: cannot add {added} to {sorted(current)}"
            )
        expr = f"({expr} JOIN {table_ref(alias_map[added])} ON {' AND '.join(predicates)})"
        current.add(added)
    return expr


def replace_from_clause(sql: str, spec: QuerySpec, join_expr: str) -> str:
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
        if all(
            re.search(rf"\b{re.escape(alias.table.lower())}\b", candidate)
            for alias in spec.aliases
        ):
            candidates.append((end - start, start, end))
    if not candidates:
        raise ValueError(f"could not find target FROM clause for {spec.query_id}")
    _, start, end = min(candidates)
    return sql[:start] + f"\n    {join_expr}\n" + sql[end:]


def explicit_join_sql(sql: str, spec: QuerySpec, path: list[str]) -> str:
    return replace_from_clause(sql, spec, build_join_from_path(spec, path))


def statement_prefix(args: argparse.Namespace, variant: VariantSpec, trace_file: Path | None = None) -> list[str]:
    statements = [
        f"SET temp_directory={sql_string_literal(args.temp_directory)};",
        f"SET max_temp_directory_size={sql_string_literal(args.max_temp_directory_size)};",
        f"SET max_memory={sql_string_literal(args.max_memory)};",
        f"SET threads={args.threads};",
    ]
    if variant.settings_kind in {"sql_original", "explicit_join_order"}:
        statements.append("SET disabled_optimizers='join_order';")
    elif variant.settings_kind == "ikkbz_top1":
        statements.extend(
            [
                "SET adl_linearize_join_order = true;",
                "SET adl_ikkbz_k = 1;",
            ]
        )
    elif variant.settings_kind == "neuso_runtime":
        command = args.neuso_sidecar_command
        if trace_file is not None and "--trace-file" not in command:
            command += f" --trace-file {trace_file}"
        statements.extend(
            [
                f"SET adl_neuso_sidecar_command = {sql_string_literal(command)};",
                f"SET adl_neuso_sidecar_host = {sql_string_literal(args.neuso_sidecar_host)};",
                f"SET adl_neuso_sidecar_port = {args.neuso_sidecar_port};",
                f"SET adl_neuso_sidecar_timeout_ms = {args.neuso_sidecar_timeout_ms};",
                "SET adl_linearize_join_order = true;",
                "SET adl_ikkbz_k = 1;",
                "SET adl_neuso_runtime_enabled = true;",
            ]
        )
    return statements


def session_sql(
    query_sql: str,
    args: argparse.Namespace,
    variant: VariantSpec,
    *,
    explain: bool,
    profile_path: Path | None = None,
    trace_file: Path | None = None,
) -> str:
    statements = statement_prefix(args, variant, trace_file=trace_file)
    if profile_path is not None:
        statements.extend(
            [
                "PRAGMA enable_profiling='json';",
                "SET profiling_mode='detailed';",
                f"PRAGMA profile_output={sql_string_literal(profile_path.as_posix())};",
            ]
        )
    query = query_sql.rstrip().rstrip(";")
    statements.append(("EXPLAIN " if explain else "") + query + ";")
    return "\n".join(statements)


def sql_for_variant(sql: str, spec: QuerySpec, variant: VariantSpec) -> str:
    if variant.settings_kind == "explicit_join_order":
        return explicit_join_sql(sql, spec, variant.join_path)
    return sql


def copy_query_to_csv_statement(query_sql: str, output_path: Path) -> str:
    query = query_sql.rstrip().rstrip(";")
    return (
        f"COPY ({query}) TO {sql_string_literal(output_path.as_posix())} "
        "(HEADER, DELIMITER ',');"
    )


def profile_time_ms(data: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)):
            return float(value) * 1000.0
    return None


def detailed_profile_metrics(profile_path: Path) -> dict[str, float | bool | str | None]:
    result: dict[str, float | bool | str | None] = {
        "profile_available": False,
        "profile_parse_error": None,
    }
    for field in PROFILE_PHASE_KEYS:
        result[field] = None
    result["qo_plan_time_ms"] = None
    result["physical_execution_time_ms"] = None
    if not profile_path.exists():
        result["profile_parse_error"] = "profile_missing"
        return result
    try:
        data = json.loads(profile_path.read_text())
    except json.JSONDecodeError:
        result["profile_parse_error"] = "profile_json_decode_error"
        return result
    if not isinstance(data, dict):
        result["profile_parse_error"] = "profile_not_object"
        return result

    result["profile_available"] = True
    for field, keys in PROFILE_PHASE_KEYS.items():
        result[field] = profile_time_ms(data, keys)

    qo_parts = [
        result["parser_time_ms"],
        result["planner_time_ms"],
        result["optimizer_time_ms"],
        result["physical_planner_time_ms"],
    ]
    if any(isinstance(value, (int, float)) for value in qo_parts):
        result["qo_plan_time_ms"] = sum(
            float(value) for value in qo_parts if isinstance(value, (int, float))
        )

    latency = result["query_latency_ms"]
    qo_plan_time = result["qo_plan_time_ms"]
    if isinstance(latency, (int, float)) and isinstance(qo_plan_time, (int, float)):
        result["physical_execution_time_ms"] = max(0.0, float(latency) - float(qo_plan_time))
    return result


def numeric_metric(metrics: dict[str, float | bool | str | None], key: str) -> float | None:
    value = metrics.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def collect_metric_sample(
    buckets: dict[str, list[float]],
    metrics: dict[str, float | bool | str | None],
    key: str,
) -> None:
    value = numeric_metric(metrics, key)
    if value is not None:
        buckets.setdefault(key, []).append(value)


def p50_metric(buckets: dict[str, list[float]], key: str) -> float | None:
    return percentile(buckets.get(key, []), 0.50)


def table_exists_sql(tables: Iterable[str]) -> str:
    table_list = ",".join(sql_string_literal(table) for table in sorted(set(tables)))
    return (
        "SELECT table_name FROM information_schema.tables "
        f"WHERE lower(table_name) IN ({table_list}) ORDER BY table_name;"
    )


def ensure_job_tables(duckdb: Path, database: Path, specs: list[QuerySpec], timeout: int) -> None:
    required = {alias.table.lower() for spec in specs for alias in spec.aliases}
    code, out, err, _ = duckdb_run(
        duckdb,
        database,
        table_exists_sql(required),
        timeout,
        csv_output=True,
    )
    if code != 0:
        raise RuntimeError(err.strip() or "failed to inspect JOB/IMDB tables")
    rows = list(csv.reader(io.StringIO(out)))
    found = {row[0].lower() for row in rows[1:] if row}
    missing = sorted(required - found)
    if missing:
        raise RuntimeError(
            "JOB/IMDB database is missing required tables: " + ", ".join(missing)
        )


def build_variants(
    spec: QuerySpec,
    *,
    random_endpoint_paths: int,
    seed: int,
    include_neuso: bool,
) -> list[VariantSpec]:
    variants = [
        VariantSpec(
            query_id=spec.query_id,
            variant_id=f"{spec.query_id}:duckdb_default",
            baseline_kind="duckdb_default",
            executable=True,
            settings_kind="duckdb_default",
            join_path=[],
            endpoint_sides=[],
            seed=None,
        ),
        VariantSpec(
            query_id=spec.query_id,
            variant_id=f"{spec.query_id}:sql_original",
            baseline_kind="sql_original",
            executable=True,
            settings_kind="sql_original",
            join_path=[alias.alias for alias in spec.aliases],
            endpoint_sides=[],
            seed=None,
            note="Uses disabled_optimizers='join_order' as an original-order approximation.",
        ),
        VariantSpec(
            query_id=spec.query_id,
            variant_id=f"{spec.query_id}:ikkbz_top1_export",
            baseline_kind="ikkbz_top1_export",
            executable=True,
            settings_kind="ikkbz_top1",
            join_path=[],
            endpoint_sides=[],
            seed=None,
            note="Exports IKKBZ top-1 metadata; DuckDB still chooses the plan.",
        ),
    ]
    if include_neuso:
        variants.append(
            VariantSpec(
                query_id=spec.query_id,
                variant_id=f"{spec.query_id}:adl_opt_applied",
                baseline_kind="adl_opt_applied",
                executable=True,
                settings_kind="neuso_runtime",
                join_path=[],
                endpoint_sides=[],
                seed=None,
                note="Applies the NeuSO runtime response as a left-deep join order.",
            )
        )

    connected_path, _ = greedy_connected_path(spec)
    linear_order, _ = folded_linear_order(connected_path)
    linear_order_id = "fixture_folded_connected_degree"
    for idx in range(random_endpoint_paths):
        path_seed = seed + idx
        endpoint_path, _ = random_endpoint_path(
            spec,
            linear_order_id,
            linear_order,
            random.Random(path_seed),
            idx,
            path_seed,
        )
        variants.append(
            VariantSpec(
                query_id=spec.query_id,
                variant_id=endpoint_path["path_id"],
                baseline_kind="random_endpoint",
                executable=bool(endpoint_path["valid"]),
                settings_kind="explicit_join_order",
                join_path=endpoint_path["join_path"],
                endpoint_sides=endpoint_path["endpoint_sides"],
                seed=path_seed,
                note="Random endpoint path applied as an explicit JOIN tree when valid.",
                path_valid=bool(endpoint_path["valid"]),
                path_failure_reason=endpoint_path.get("failure_reason"),
                covered_alias_count=endpoint_path.get("covered_alias_count"),
            )
        )
    return variants


def variant_row(variant: VariantSpec) -> dict:
    return {
        "query_id": variant.query_id,
        "variant_id": variant.variant_id,
        "baseline_kind": variant.baseline_kind,
        "executable": variant.executable,
        "settings_kind": variant.settings_kind,
        "join_path": variant.join_path,
        "endpoint_sides": variant.endpoint_sides,
        "seed": variant.seed,
        "note": variant.note,
        "path_valid": variant.path_valid,
        "path_failure_reason": variant.path_failure_reason,
        "covered_alias_count": variant.covered_alias_count,
        "updated": UPDATED,
    }


def filter_variants(variants: list[VariantSpec], baseline_kinds: list[str]) -> list[VariantSpec]:
    if "all" in baseline_kinds:
        return variants
    selected = {"adl_opt_applied" if kind == "neuso_runtime_validate" else kind for kind in baseline_kinds}
    return [variant for variant in variants if variant.baseline_kind in selected]


def query_graph_row(spec: QuerySpec, threshold: int) -> dict:
    return {
        "query_id": spec.query_id,
        "workload": WORKLOAD,
        "workload_query": spec.workload_query,
        "scale_factor": None,
        "sql_file": spec.sql_file,
        "aliases": [alias.__dict__ for alias in spec.aliases],
        "edges": [edge.__dict__ for edge in spec.edges],
        "filters": list(spec.filters),
        "relation_count": len(spec.aliases),
        "join_edge_count": len(spec.edges),
        "large_join_threshold": threshold,
        "large_join_candidate": len(spec.aliases) >= threshold,
        "regular_inner_pair_graph": is_regular_inner_pair_graph(spec),
        "connected_join_graph": is_connected_join_graph(spec),
        "estimated_cardinality_available": False,
        "estimated_cost_available": False,
        "sql_hash": spec.sql_hash,
        "updated": UPDATED,
    }


def workload_row(spec: QuerySpec, selected: bool, threshold: int) -> dict:
    regular_pair_graph = is_regular_inner_pair_graph(spec)
    connected_join_graph = is_connected_join_graph(spec)
    return {
        "query_id": spec.query_id,
        "workload": WORKLOAD,
        "workload_query": spec.workload_query,
        "sql_file": spec.sql_file,
        "selected": selected,
        "selection_reason": selection_reason(spec, threshold),
        "relation_count": len(spec.aliases),
        "join_edge_count": len(spec.edges),
        "large_join_threshold": threshold,
        "regular_inner_pair_graph": regular_pair_graph,
        "connected_join_graph": connected_join_graph,
        "sql_hash": spec.sql_hash,
        "updated": UPDATED,
    }


def is_regular_inner_pair_graph(spec: QuerySpec) -> bool:
    # JOB SQL in this repository is comma FROM + WHERE predicates. The static
    # parser only emits singleton equality edges, so the remaining practical
    # guard is that every alias participates in at least one edge.
    aliases = {alias.alias for alias in spec.aliases}
    edge_aliases = {edge.left for edge in spec.edges} | {edge.right for edge in spec.edges}
    return bool(aliases) and aliases <= edge_aliases


def is_connected_join_graph(spec: QuerySpec) -> bool:
    aliases = {alias.alias for alias in spec.aliases}
    if not aliases:
        return False
    seen = {next(iter(aliases))}
    changed = True
    while changed:
        changed = False
        for edge in spec.edges:
            if edge.left in seen and edge.right not in seen:
                seen.add(edge.right)
                changed = True
            if edge.right in seen and edge.left not in seen:
                seen.add(edge.left)
                changed = True
    return seen == aliases


def should_select_query(spec: QuerySpec, threshold: int) -> bool:
    return (
        len(spec.aliases) >= threshold
        and is_regular_inner_pair_graph(spec)
        and is_connected_join_graph(spec)
    )


def selection_reason(spec: QuerySpec, threshold: int) -> str:
    if len(spec.aliases) < threshold:
        return "below_large_join_threshold"
    if not is_regular_inner_pair_graph(spec):
        return "not_regular_inner_pair_graph"
    if not is_connected_join_graph(spec):
        return "disconnected_join_graph"
    return "selected_large_regular_inner_pair_graph"


def skipped_plan_result(spec: QuerySpec, variant: VariantSpec, reason: str) -> dict:
    return {
        "query_id": spec.query_id,
        "variant_id": variant.variant_id,
        "baseline_kind": variant.baseline_kind,
        "measurement_source": "duckdb_detailed_profile",
        "plan_latency_samples_ms": [],
        "plan_latency_p50_ms": None,
        "plan_latency_p95_ms": None,
        "plan_latency_p99_ms": None,
        "plan_latency_max_ms": None,
        "qo_plan_time_samples_ms": [],
        "duckdb_wall_time_samples_ms": [],
        "parser_time_p50_ms": None,
        "planner_time_p50_ms": None,
        "planner_binding_time_p50_ms": None,
        "optimizer_time_p50_ms": None,
        "join_order_optimizer_time_p50_ms": None,
        "physical_planner_time_p50_ms": None,
        "physical_planner_create_plan_time_p50_ms": None,
        "explain_hash": None,
        "physical_plan_available": False,
        "sidecar_latency_ms": None,
        "model_latency_ms": None,
        "optimizer_time_ms": None,
        "profile_available": False,
        "profile_parse_error": None,
        "failure_reason": reason,
    }


def skipped_run_result(spec: QuerySpec, variant: VariantSpec, reason: str) -> dict:
    return {
        "query_id": spec.query_id,
        "variant_id": variant.variant_id,
        "baseline_kind": variant.baseline_kind,
        "join_path": variant.join_path,
        "sql_hash": spec.sql_hash,
        "explain_hash": None,
        "source_variant_id": None,
        "plan_control_valid": False,
        "correct": False,
        "row_count": None,
        "result_checksum": None,
        "execution_latency_samples_ms": [],
        "execution_latency_p50_ms": None,
        "execution_latency_p95_ms": None,
        "execution_latency_p99_ms": None,
        "execution_latency_max_ms": None,
        "measurement_source": "duckdb_detailed_profile",
        "duckdb_wall_time_samples_ms": [],
        "query_latency_samples_ms": [],
        "qo_plan_time_samples_ms": [],
        "execution_cpu_time_samples_ms": [],
        "parser_time_samples_ms": [],
        "planner_time_samples_ms": [],
        "planner_binding_time_samples_ms": [],
        "optimizer_time_samples_ms": [],
        "join_order_optimizer_time_samples_ms": [],
        "physical_planner_time_samples_ms": [],
        "physical_planner_create_plan_time_samples_ms": [],
        "optimizer_time_ms": None,
        "join_order_optimizer_time_ms": None,
        "qo_plan_time_ms": None,
        "execution_time_ms": None,
        "speedup_vs_default": None,
        "regret_vs_sampled_oracle": None,
        "timeout": False,
        "profile_available": False,
        "profile_parse_error": None,
        "failure_reason": reason,
    }


def measure_plan_latency(
    duckdb: Path,
    database: Path,
    sql: str,
    spec: QuerySpec,
    variant: VariantSpec,
    args: argparse.Namespace,
    profiles_dir: Path,
    traces_dir: Path,
) -> dict:
    if not variant.executable:
        return skipped_plan_result(spec, variant, variant.path_failure_reason or "invalid_endpoint_path")
    try:
        variant_sql = sql_for_variant(sql, spec, variant)
    except ValueError as exc:
        return skipped_plan_result(spec, variant, str(exc))
    wall_samples: list[float] = []
    last_out = ""
    trace_file = traces_dir / f"{variant.variant_id.replace(':', '_')}_plan_trace.json"
    try:
        statements = statement_prefix(args, variant, trace_file=trace_file)
        query = variant_sql.rstrip().rstrip(";")
        statements.append(f"EXPLAIN {query};")
        code, out, err, latency_ms = duckdb_run(
            duckdb,
            database,
            "\n".join(statements),
            args.timeout,
            csv_output=False,
        )
        wall_samples.append(latency_ms)
        last_out = out
        if code != 0:
            return {
                **skipped_plan_result(spec, variant, err.strip() or "EXPLAIN failed"),
                "duckdb_wall_time_samples_ms": wall_samples,
            }
    except subprocess.TimeoutExpired:
        return {
            **skipped_plan_result(spec, variant, "timeout"),
            "duckdb_wall_time_samples_ms": wall_samples,
        }
    return {
        "query_id": spec.query_id,
        "variant_id": variant.variant_id,
        "baseline_kind": variant.baseline_kind,
        "measurement_source": "duckdb_detailed_profile",
        "plan_latency_samples_ms": [],
        "plan_latency_p50_ms": None,
        "plan_latency_p95_ms": None,
        "plan_latency_p99_ms": None,
        "plan_latency_max_ms": None,
        "qo_plan_time_samples_ms": [],
        "duckdb_wall_time_samples_ms": wall_samples,
        "parser_time_p50_ms": None,
        "planner_time_p50_ms": None,
        "planner_binding_time_p50_ms": None,
        "optimizer_time_p50_ms": None,
        "join_order_optimizer_time_p50_ms": None,
        "physical_planner_time_p50_ms": None,
        "physical_planner_create_plan_time_p50_ms": None,
        "explain_hash": sha256_text(last_out),
        "physical_plan_available": True,
        "sidecar_latency_ms": None,
        "model_latency_ms": read_trace_latency(trace_file),
        "optimizer_time_ms": None,
        "profile_available": False,
        "profile_parse_error": "filled_from_execution_profile",
        "failure_reason": None,
    }


def read_trace_latency(trace_file: Path) -> float | None:
    if not trace_file.exists():
        return None
    try:
        trace = json.loads(trace_file.read_text())
    except json.JSONDecodeError:
        return None
    response = trace.get("response") if isinstance(trace, dict) else None
    latency = response.get("latency_ms") if isinstance(response, dict) else None
    return float(latency) if isinstance(latency, (int, float)) else None


def fill_plan_result_from_execution_profile(plan_row: dict, run_row: dict) -> dict:
    if plan_row.get("failure_reason") is not None:
        return plan_row
    samples = list(run_row.get("qo_plan_time_samples_ms") or [])
    stats = latency_stats(samples)
    plan_row.update(
        {
            "plan_latency_samples_ms": samples,
            "plan_latency_p50_ms": stats["p50_ms"],
            "plan_latency_p95_ms": stats["p95_ms"],
            "plan_latency_p99_ms": stats["p99_ms"],
            "plan_latency_max_ms": stats["max_ms"],
            "qo_plan_time_samples_ms": samples,
            "parser_time_p50_ms": percentile(run_row.get("parser_time_samples_ms") or [], 0.50),
            "planner_time_p50_ms": percentile(run_row.get("planner_time_samples_ms") or [], 0.50),
            "planner_binding_time_p50_ms": percentile(
                run_row.get("planner_binding_time_samples_ms") or [], 0.50
            ),
            "optimizer_time_p50_ms": percentile(
                run_row.get("optimizer_time_samples_ms") or [], 0.50
            ),
            "join_order_optimizer_time_p50_ms": percentile(
                run_row.get("join_order_optimizer_time_samples_ms") or [], 0.50
            ),
            "physical_planner_time_p50_ms": percentile(
                run_row.get("physical_planner_time_samples_ms") or [], 0.50
            ),
            "physical_planner_create_plan_time_p50_ms": percentile(
                run_row.get("physical_planner_create_plan_time_samples_ms") or [], 0.50
            ),
            "optimizer_time_ms": run_row.get("optimizer_time_ms"),
            "profile_available": bool(samples),
            "profile_parse_error": None if samples else run_row.get("profile_parse_error"),
        }
    )
    return plan_row


def measure_execution(
    duckdb: Path,
    database: Path,
    sql: str,
    spec: QuerySpec,
    variant: VariantSpec,
    args: argparse.Namespace,
    profiles_dir: Path,
    traces_dir: Path,
) -> dict:
    if not variant.executable:
        return skipped_run_result(spec, variant, variant.path_failure_reason or "invalid_endpoint_path")
    try:
        variant_sql = sql_for_variant(sql, spec, variant)
    except ValueError as exc:
        return skipped_run_result(spec, variant, str(exc))
    trace_file = traces_dir / f"{variant.variant_id.replace(':', '_')}_run_trace.json"
    try:
        samples: list[float] = []
        wall_samples: list[float] = []
        query_latency_samples: list[float] = []
        qo_plan_samples: list[float] = []
        execution_cpu_samples: list[float] = []
        metric_buckets: dict[str, list[float]] = {}
        last_code = 0
        last_err = ""
        last_profile_error = None
        results_dir = profiles_dir / "results"
        results_dir.mkdir(exist_ok=True)
        statements = statement_prefix(args, variant, trace_file=trace_file)
        created_result_paths: list[Path] = []
        query_statement = variant_sql.rstrip().rstrip(";") + ";"
        for warmup_idx in range(args.warmup_runs):
            statements.append(query_statement)
        statements.extend(["SET profiling_mode='detailed';", "PRAGMA enable_profiling='json';"])
        profile_paths: list[Path] = []
        for run_idx in range(args.measure_runs):
            profile_path = profiles_dir / f"{variant.variant_id.replace(':', '_')}_run{run_idx}.json"
            if profile_path.exists():
                profile_path.unlink()
            profile_paths.append(profile_path)
            statements.append(f"PRAGMA profile_output={sql_string_literal(profile_path.as_posix())};")
            statements.append(query_statement)
        checksum_path = results_dir / f"{variant.variant_id.replace(':', '_')}_checksum.csv"
        if checksum_path.exists():
            checksum_path.unlink()
        created_result_paths.append(checksum_path)
        statements.append("PRAGMA disable_profiling;")
        statements.append(copy_query_to_csv_statement(variant_sql, checksum_path))
        last_code, _out, last_err, latency_ms = duckdb_run(
            duckdb,
            database,
            "\n".join(statements),
            args.timeout * max(1, args.warmup_runs + args.measure_runs),
            csv_output=False,
        )
        wall_samples.append(latency_ms)
        if last_code != 0:
            return {
                **skipped_run_result(spec, variant, last_err.strip() or "execution failed"),
                "execution_latency_samples_ms": samples,
                "duckdb_wall_time_samples_ms": wall_samples,
                "query_latency_samples_ms": query_latency_samples,
                "qo_plan_time_samples_ms": qo_plan_samples,
                "execution_cpu_time_samples_ms": execution_cpu_samples,
            }
        for profile_path in profile_paths:
            metrics = detailed_profile_metrics(profile_path)
            last_profile_error = metrics.get("profile_parse_error")
            collect_metric_sample(metric_buckets, metrics, "optimizer_time_ms")
            collect_metric_sample(metric_buckets, metrics, "join_order_optimizer_time_ms")
            collect_metric_sample(metric_buckets, metrics, "physical_execution_time_ms")
            collect_metric_sample(metric_buckets, metrics, "qo_plan_time_ms")
            collect_metric_sample(metric_buckets, metrics, "query_latency_ms")
            collect_metric_sample(metric_buckets, metrics, "execution_cpu_time_ms")
            collect_metric_sample(metric_buckets, metrics, "parser_time_ms")
            collect_metric_sample(metric_buckets, metrics, "planner_time_ms")
            collect_metric_sample(metric_buckets, metrics, "planner_binding_time_ms")
            collect_metric_sample(metric_buckets, metrics, "physical_planner_time_ms")
            collect_metric_sample(metric_buckets, metrics, "physical_planner_create_plan_time_ms")
            execution_sample = numeric_metric(metrics, "physical_execution_time_ms")
            query_latency_sample = numeric_metric(metrics, "query_latency_ms")
            qo_plan_sample = numeric_metric(metrics, "qo_plan_time_ms")
            cpu_sample = numeric_metric(metrics, "execution_cpu_time_ms")
            if execution_sample is not None:
                samples.append(execution_sample)
            if query_latency_sample is not None:
                query_latency_samples.append(query_latency_sample)
            if qo_plan_sample is not None:
                qo_plan_samples.append(qo_plan_sample)
            if cpu_sample is not None:
                execution_cpu_samples.append(cpu_sample)
    except subprocess.TimeoutExpired:
        return {
            **skipped_run_result(spec, variant, "timeout"),
            "timeout": True,
        }
    if not checksum_path.exists():
        return skipped_run_result(spec, variant, "result_csv_missing")
    row_count, checksum = result_checksum(checksum_path.read_text())
    for result_path in created_result_paths:
        try:
            result_path.unlink()
        except FileNotFoundError:
            pass
    stats = latency_stats(samples)
    return {
        "query_id": spec.query_id,
        "variant_id": variant.variant_id,
        "baseline_kind": variant.baseline_kind,
        "join_path": variant.join_path,
        "sql_hash": spec.sql_hash,
        "explain_hash": None,
        "source_variant_id": None,
        "plan_control_valid": True,
        "correct": False,
        "row_count": row_count,
        "result_checksum": checksum,
        "execution_latency_samples_ms": samples,
        "execution_latency_p50_ms": stats["p50_ms"],
        "execution_latency_p95_ms": stats["p95_ms"],
        "execution_latency_p99_ms": stats["p99_ms"],
        "execution_latency_max_ms": stats["max_ms"],
        "measurement_source": "duckdb_detailed_profile",
        "duckdb_wall_time_samples_ms": wall_samples,
        "query_latency_samples_ms": query_latency_samples,
        "qo_plan_time_samples_ms": qo_plan_samples,
        "execution_cpu_time_samples_ms": execution_cpu_samples,
        "parser_time_samples_ms": metric_buckets.get("parser_time_ms", []),
        "planner_time_samples_ms": metric_buckets.get("planner_time_ms", []),
        "planner_binding_time_samples_ms": metric_buckets.get("planner_binding_time_ms", []),
        "optimizer_time_samples_ms": metric_buckets.get("optimizer_time_ms", []),
        "join_order_optimizer_time_samples_ms": metric_buckets.get(
            "join_order_optimizer_time_ms", []
        ),
        "physical_planner_time_samples_ms": metric_buckets.get("physical_planner_time_ms", []),
        "physical_planner_create_plan_time_samples_ms": metric_buckets.get(
            "physical_planner_create_plan_time_ms", []
        ),
        "optimizer_time_ms": p50_metric(metric_buckets, "optimizer_time_ms"),
        "join_order_optimizer_time_ms": p50_metric(metric_buckets, "join_order_optimizer_time_ms"),
        "qo_plan_time_ms": percentile(qo_plan_samples, 0.50),
        "execution_time_ms": percentile(samples, 0.50),
        "speedup_vs_default": None,
        "regret_vs_sampled_oracle": None,
        "timeout": False,
        "profile_available": bool(samples),
        "profile_parse_error": None if samples else last_profile_error,
        "failure_reason": None,
    }


def apply_correctness_and_scores(run_rows: list[dict]) -> list[dict]:
    by_query: dict[str, list[dict]] = {}
    for row in run_rows:
        by_query.setdefault(row["query_id"], []).append(row)
    correctness_rows = []
    for query_id, rows in by_query.items():
        default = next((row for row in rows if row["baseline_kind"] == "duckdb_default"), None)
        default_key = None
        default_latency = None
        if default and default["failure_reason"] is None:
            default_key = (default["row_count"], default["result_checksum"])
            default_latency = default["execution_latency_p50_ms"]
        comparable = []
        for row in rows:
            if row["failure_reason"] is None and default_key is not None:
                row["correct"] = (row["row_count"], row["result_checksum"]) == default_key
                if not row["correct"]:
                    row["failure_reason"] = "checksum_mismatch"
            if row["correct"] and row["execution_latency_p50_ms"] is not None:
                if default_latency:
                    row["speedup_vs_default"] = default_latency / row["execution_latency_p50_ms"]
                comparable.append(row)
            correctness_rows.append(
                {
                    "query_id": query_id,
                    "variant_id": row["variant_id"],
                    "baseline_kind": row["baseline_kind"],
                    "correct": row["correct"],
                    "row_count": row["row_count"],
                    "result_checksum": row["result_checksum"],
                    "reference_variant_id": default["variant_id"] if default else None,
                    "failure_reason": row["failure_reason"],
                }
            )
        oracle = min(comparable, key=lambda row: row["execution_latency_p50_ms"], default=None)
        if oracle and oracle["execution_latency_p50_ms"]:
            for row in comparable:
                row["regret_vs_sampled_oracle"] = (
                    row["execution_latency_p50_ms"] / oracle["execution_latency_p50_ms"] - 1.0
                )
    return correctness_rows


def aggregate_summary(
    args: argparse.Namespace,
    workload_rows: list[dict],
    variants: list[dict],
    plan_rows: list[dict],
    run_rows: list[dict],
    executed: bool,
) -> dict:
    plan_samples = [
        sample
        for row in plan_rows
        for sample in row.get("plan_latency_samples_ms", [])
        if row.get("failure_reason") is None
    ]
    execution_samples = [
        sample
        for row in run_rows
        for sample in row.get("execution_latency_samples_ms", [])
        if row.get("failure_reason") is None and row.get("correct")
    ]
    speedups = [
        row["speedup_vs_default"]
        for row in run_rows
        if row.get("speedup_vs_default") and row.get("baseline_kind") != "duckdb_default"
    ]
    regrets = [
        row["regret_vs_sampled_oracle"]
        for row in run_rows
        if row.get("regret_vs_sampled_oracle") is not None
        and row.get("baseline_kind") != "duckdb_default"
    ]
    wins = 0
    losses = 0
    for row in run_rows:
        if row.get("baseline_kind") == "duckdb_default" or not row.get("speedup_vs_default"):
            continue
        if row["speedup_vs_default"] > 1.0:
            wins += 1
        elif row["speedup_vs_default"] < 1.0:
            losses += 1
    regret_factors = [1.0 + value for value in regrets]
    geomean_regret_factor = geomean(regret_factors)
    return {
        "updated": UPDATED,
        "run_id": args.run_id,
        "dataset": WORKLOAD,
        "executed": executed,
        "input_query_count": len(workload_rows),
        "query_count": sum(1 for row in workload_rows if row.get("selected")),
        "variant_count": len(variants),
        "correctness_failures": sum(
            1 for row in run_rows if row.get("failure_reason") == "checksum_mismatch"
        ),
        "timeout_count": sum(1 for row in run_rows if row.get("timeout")),
        "fallback_count": sum(
            1
            for row in run_rows
            if row.get("failure_reason") in {"apply_not_implemented", "invalid_endpoint_path"}
        ),
        "execution_failure_count": sum(
            1 for row in run_rows if row.get("failure_reason") == "execution failed"
        ),
        "failed_variant_count": sum(1 for row in run_rows if row.get("failure_reason")),
        "plan_latency": latency_stats(plan_samples),
        "execution_latency": latency_stats(execution_samples),
        "plan_quality": {
            "geomean_speedup_vs_default": geomean(speedups),
            "geomean_regret_factor_vs_sampled_oracle": geomean_regret_factor,
            "geomean_regret_vs_sampled_oracle": (
                geomean_regret_factor - 1.0
                if geomean_regret_factor is not None
                else None
            ),
            "win_count": wins,
            "loss_count": losses,
        },
        "queries": [row["query_id"] for row in workload_rows if row.get("selected")],
    }


def write_summary_md(output: Path, summary: dict) -> None:
    lines = [
        "# ADL-OPT JOB/IMDB Benchmark Summary",
        "",
        f"English TL;DR: Measured JOB/IMDB plan latency and execution latency for {summary['query_count']} queries.",
        "",
        f"Updated: {UPDATED}",
        "",
        "Key terms: ADL-OPT, JOB, IMDB, plan latency, plan quality, P50, P95, P99",
        "",
        f"- Executed DuckDB: {summary['executed']}",
        f"- Query count: {summary['query_count']}",
        f"- Variant count: {summary['variant_count']}",
        f"- Correctness failures: {summary['correctness_failures']}",
        f"- Timeout count: {summary['timeout_count']}",
        f"- Fallback/skipped variants: {summary['fallback_count']}",
        f"- Execution failures: {summary['execution_failure_count']}",
        f"- Failed variants: {summary['failed_variant_count']}",
        f"- Plan latency P50/P95/P99/max ms: {summary['plan_latency']} (DuckDB detailed profiling)",
        f"- Physical execution latency P50/P95/P99/max ms: {summary['execution_latency']} (profile latency minus plan phases)",
        f"- Plan quality: {summary['plan_quality']}",
    ]
    (output / "summary.md").write_text("\n".join(lines) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="DuckDB repository root")
    parser.add_argument("--output", required=True, help="Output directory or run root")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--queries", nargs="+", default=list(DEFAULT_QUERIES))
    parser.add_argument("--large-join-threshold", type=int, default=12)
    parser.add_argument("--random-endpoint-paths", type=int, default=5)
    parser.add_argument(
        "--baseline-kinds",
        nargs="+",
        default=["all"],
        choices=["all", *sorted(BASELINE_KINDS)],
        help="Variant families to execute. Use duckdb_default for a default-optimizer-only run.",
    )
    parser.add_argument("--seed", type=int, default=20260510)
    parser.add_argument("--duckdb", help="DuckDB CLI/binary path")
    parser.add_argument("--database", default="/tmp/adl-opt-job-imdb.duckdb")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument(
        "--temp-directory",
        help="DuckDB temp directory. Defaults to <database>.tmp-safe when executing.",
    )
    parser.add_argument("--max-temp-directory-size", default="8GB")
    parser.add_argument("--max-memory", default="4GB")
    parser.add_argument("--warmup-runs", type=int, default=1)
    parser.add_argument("--measure-runs", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--include-neuso", action="store_true")
    parser.add_argument(
        "--neuso-sidecar-command",
        default="PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_sidecar.py",
    )
    parser.add_argument("--neuso-sidecar-host", default="127.0.0.1")
    parser.add_argument("--neuso-sidecar-port", type=int, default=8765)
    parser.add_argument("--neuso-sidecar-timeout-ms", type=int, default=10000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo = Path(args.repo).resolve()
    if args.execute and not args.temp_directory:
        args.temp_directory = str(Path(args.database).with_suffix(Path(args.database).suffix + ".tmp-safe"))
    elif not args.temp_directory:
        args.temp_directory = str((Path(args.output).resolve() / args.run_id / "tmp").resolve())
    Path(args.temp_directory).mkdir(parents=True, exist_ok=True)
    output_root = Path(args.output).resolve()
    output = output_root if output_root.name == args.run_id else output_root / args.run_id
    output.mkdir(parents=True, exist_ok=True)
    traces_dir = output / "traces"
    profiles_dir = output / "profiles"
    traces_dir.mkdir(exist_ok=True)
    profiles_dir.mkdir(exist_ok=True)

    specs = [parse_query(repo, query) for query in args.queries]
    selected_specs = [spec for spec in specs if should_select_query(spec, args.large_join_threshold)]

    workload_rows = [
        workload_row(spec, spec in selected_specs, args.large_join_threshold)
        for spec in specs
    ]
    query_graph_rows = [query_graph_row(spec, args.large_join_threshold) for spec in selected_specs]
    variants_by_query = {
        spec.query_id: filter_variants(
            build_variants(
                spec,
                random_endpoint_paths=args.random_endpoint_paths,
                seed=args.seed,
                include_neuso=args.include_neuso,
            ),
            args.baseline_kinds,
        )
        for spec in selected_specs
    }
    variant_rows = [
        variant_row(variant)
        for variants in variants_by_query.values()
        for variant in variants
    ]

    run_config = {
        "updated": UPDATED,
        "run_id": args.run_id,
        "workload": WORKLOAD,
        "queries": args.queries,
        "selected_queries": [spec.workload_query for spec in selected_specs],
        "large_join_threshold": args.large_join_threshold,
        "baseline_kinds": args.baseline_kinds,
        "execute": args.execute,
        "duckdb": args.duckdb,
        "database": args.database,
        "threads": args.threads,
        "temp_directory": args.temp_directory,
        "max_temp_directory_size": args.max_temp_directory_size,
        "max_memory": args.max_memory,
        "warmup_runs": args.warmup_runs,
        "measure_runs": args.measure_runs,
        "timeout": args.timeout,
        "include_neuso": args.include_neuso,
        "joblight": "not_used",
        "measurement_source": "duckdb_detailed_profile",
        "plan_latency_semantics": "SQL parser/planner/optimizer/physical-planner timing from detailed profiling",
        "execution_latency_semantics": "DuckDB profile latency minus profiled plan phases",
        "cache_policy": "warm_cache_same_session",
        "prepared_statement": False,
    }
    (output / "run_config.json").write_text(json.dumps(run_config, indent=2, sort_keys=True) + "\n")

    plan_rows: list[dict] = []
    run_rows: list[dict] = []
    if args.execute:
        if not args.duckdb:
            raise RuntimeError("--duckdb is required with --execute")
        duckdb = Path(args.duckdb)
        database = Path(args.database)
        if selected_specs:
            ensure_job_tables(duckdb, database, selected_specs, args.timeout)
        for spec in selected_specs:
            sql = read_sql(repo, spec)
            for variant in variants_by_query[spec.query_id]:
                plan_row = measure_plan_latency(
                    duckdb, database, sql, spec, variant, args, profiles_dir, traces_dir
                )
                run_row = measure_execution(
                    duckdb, database, sql, spec, variant, args, profiles_dir, traces_dir
                )
                run_row["explain_hash"] = plan_row.get("explain_hash")
                plan_row = fill_plan_result_from_execution_profile(plan_row, run_row)
                plan_rows.append(plan_row)
                run_rows.append(run_row)
    else:
        for spec in selected_specs:
            for variant in variants_by_query[spec.query_id]:
                reason = (
                    "not_executed"
                    if variant.executable
                    else variant.path_failure_reason or "invalid_endpoint_path"
                )
                plan_rows.append(skipped_plan_result(spec, variant, reason))
                run_rows.append(skipped_run_result(spec, variant, reason))

    correctness_rows = apply_correctness_and_scores(run_rows)
    summary = aggregate_summary(
        args, workload_rows, variant_rows, plan_rows, run_rows, executed=args.execute
    )

    write_jsonl(output / "workload.jsonl", workload_rows)
    write_jsonl(output / "query_graph.jsonl", query_graph_rows)
    write_jsonl(output / "variant.jsonl", variant_rows)
    write_jsonl(output / "plan_result.jsonl", plan_rows)
    write_jsonl(output / "run_result.jsonl", run_rows)
    write_jsonl(output / "correctness.jsonl", correctness_rows)
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    write_summary_md(output, summary)
    print(f"job_benchmark_runner: wrote {output}")


if __name__ == "__main__":
    main()
