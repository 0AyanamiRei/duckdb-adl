#!/usr/bin/env python3
"""Optional ADL-OPT smoke test for the NeuSO runtime bridge contract.

This script is intentionally outside DuckDB's default CI path. It verifies that
a DuckDB-like runtime join-order request can be adapted into NeuSO's
single-relation linear order interface and that the response has the shape
DuckDB should later validate before applying a forced left-deep plan.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MODEL_VERSION = "neuso_contract_smoke"
DEFAULT_TESTDATA_DIR = Path("scripts/adl_opt/testdata/neuso_runtime_bridge")
STABLE_RESPONSE_FIELDS = [
    "version",
    "request_id",
    "graph_hash",
    "status",
    "model_version",
    "join_order",
]


class SmokeError(RuntimeError):
    pass


@dataclass
class NeuSODependencies:
    torch: Any
    nx: Any
    plan_enumerator_cls: Any


@dataclass
class RegressionCase:
    case_dir: Path
    sql: str
    request: dict[str, Any]
    expected_response: dict[str, Any]


def load_neuso_dependencies() -> NeuSODependencies:
    try:
        import networkx as nx
        import torch
        from Plan.plan_enumerator import PlanEnumerator
    except ImportError as exc:
        raise SmokeError(
            "Unable to import NeuSO dependencies. Run with a NeuSO-capable Python environment, for example:\n"
            "  PYTHONPATH=NeuSO .venv/bin/python scripts/adl_opt/neuso_runtime_bridge_smoke.py "
            "--mode regression --case-dir scripts/adl_opt/testdata/neuso_runtime_bridge/chain_12"
        ) from exc
    return NeuSODependencies(torch=torch, nx=nx, plan_enumerator_cls=PlanEnumerator)


def stable_graph_hash(relations: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    relation_ids = sorted(int(item["relation_id"]) for item in relations)
    edge_pairs = sorted(
        (
            int(edge["left_relation_id"]),
            int(edge["right_relation_id"]),
            str(edge.get("join_type", "INNER")),
        )
        for edge in edges
    )
    payload = json.dumps({"relations": relation_ids, "edges": edge_pairs}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_fixture_request() -> dict[str, Any]:
    relation_count = 12
    relations = []
    for idx in range(relation_count):
        degree = 1 if idx in (0, relation_count - 1) else 2
        relations.append(
            {
                "relation_id": idx,
                "debug_label": f"r{idx}",
                "alias": f"t{idx}",
                "table": f"t{idx}",
                "base_cardinality": 1000 - idx * 10,
                "estimated_cardinality": 1000 - idx * 10,
                "degree": degree,
            }
        )
    edges = []
    for idx in range(relation_count - 1):
        edges.append(
            {
                "edge_id": idx,
                "left_relation_id": idx,
                "right_relation_id": idx + 1,
                "join_type": "INNER",
                "predicate_type": "EQUAL",
                "estimated_pair_cardinality": 100,
                "selectivity": 0.01,
                "estimated_join_cost": 1000 + idx,
            }
        )
    graph_hash = stable_graph_hash(relations, edges)
    return {
        "version": 1,
        "request_id": "fixture_chain_12",
        "graph_hash": graph_hash,
        "mode": "linear_join_order",
        "scope": {
            "relation_count": relation_count,
            "large_join_threshold": 12,
            "supported_shape": "regular_inner_pair_graph",
        },
        "relations": relations,
        "edges": edges,
    }


def build_duckdb_smoke_sql(export_path: Path) -> str:
    statements = []
    for idx in range(12):
        statements.append(
            f"CREATE OR REPLACE TABLE t{idx} AS SELECT i::INTEGER AS i FROM range(100) AS r(i);"
        )
    statements.extend(
        [
            "SET adl_linearize_join_order = true;",
            f"SET adl_linearization_output = '{str(export_path)}';",
            "SET adl_ikkbz_k = 3;",
        ]
    )
    joins = ["FROM t0"]
    for idx in range(1, 12):
        joins.append(f"JOIN t{idx} ON t{idx - 1}.i = t{idx}.i")
    statements.append("EXPLAIN SELECT count(*)\n" + "\n".join(joins) + ";")
    return "\n".join(statements) + "\n"


def run_duckdb_export(duckdb: Path, database: Path, output_dir: Path) -> dict[str, Any]:
    if not duckdb.exists():
        raise SmokeError(f"DuckDB binary does not exist: {duckdb}")
    output_dir.mkdir(parents=True, exist_ok=True)
    export_path = output_dir / "duckdb_linearization.json"
    if export_path.exists():
        export_path.unlink()
    sql = build_duckdb_smoke_sql(export_path)
    proc = subprocess.run(
        [str(duckdb), str(database)],
        input=sql,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    combined_output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        if (
            "adl_linearize_join_order" in combined_output
            or "adl_linearization_output" in combined_output
            or "adl_ikkbz_k" in combined_output
            or "unrecognized configuration parameter" in combined_output.lower()
        ):
            raise SmokeError(
                "The supplied DuckDB binary does not appear to support R5 ADL-OPT linearization export settings. "
                "Use a binary built from a branch containing adl_linearize_join_order/adl_linearization_output."
            )
        raise SmokeError(f"DuckDB export smoke failed with exit code {proc.returncode}:\n{combined_output}")
    if not export_path.exists():
        raise SmokeError(
            "DuckDB completed but did not write the expected linearization JSON. "
            "This usually means the binary lacks R5 export support or the setting did not take effect."
        )
    try:
        with export_path.open() as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise SmokeError(f"DuckDB export is not valid JSON: {export_path}") from exc


def adapt_duckdb_export(export: dict[str, Any]) -> dict[str, Any]:
    if export.get("status") != "ok":
        raise SmokeError(f"DuckDB export status is not ok: {export.get('status')}")
    relations = []
    for item in export.get("relations", []):
        relation_id = int(item["relation_id"])
        relations.append(
            {
                "relation_id": relation_id,
                "debug_label": item.get("internal_label", f"r{relation_id}"),
                "alias": item.get("internal_label", f"r{relation_id}"),
                "table": item.get("internal_label", f"r{relation_id}"),
                "base_cardinality": item.get("base_cardinality"),
                "estimated_cardinality": item.get("base_cardinality"),
                "degree": 0,
            }
        )
    relation_ids = {int(item["relation_id"]) for item in relations}
    degree = {relation_id: 0 for relation_id in relation_ids}
    edges = []
    for edge in export.get("edges", []):
        if edge.get("join_type") != "INNER":
            raise SmokeError(f"Unsupported exported edge join_type: {edge.get('join_type')}")
        left = int(edge["left_relation_id"])
        right = int(edge["right_relation_id"])
        if left not in relation_ids or right not in relation_ids:
            raise SmokeError(f"Exported edge references an unknown relation: {left}, {right}")
        degree[left] += 1
        degree[right] += 1
        edges.append(
            {
                "edge_id": int(edge.get("edge_id", len(edges))),
                "left_relation_id": left,
                "right_relation_id": right,
                "join_type": "INNER",
                "predicate_type": "REGULAR_INNER",
                "estimated_pair_cardinality": edge.get("estimated_pair_cardinality"),
                "selectivity": edge.get("selectivity"),
                "estimated_join_cost": edge.get("cout_rank"),
            }
        )
    for relation in relations:
        relation["degree"] = degree[int(relation["relation_id"])]
    graph_hash = stable_graph_hash(relations, edges)
    return {
        "version": 1,
        "request_id": "duckdb_export_smoke",
        "graph_hash": graph_hash,
        "mode": "linear_join_order",
        "scope": {
            "relation_count": int(export["relation_count"]),
            "large_join_threshold": int(export.get("large_join_threshold", 12)),
            "supported_shape": "regular_inner_pair_graph",
        },
        "relations": sorted(relations, key=lambda item: int(item["relation_id"])),
        "edges": sorted(edges, key=lambda item: int(item["edge_id"])),
    }


def validate_request(request: dict[str, Any]) -> None:
    for field in ["version", "request_id", "graph_hash", "mode", "scope", "relations", "edges"]:
        if field not in request:
            raise SmokeError(f"Request is missing required field: {field}")
    if request["mode"] != "linear_join_order":
        raise SmokeError(f"Unsupported request mode: {request['mode']}")
    scope = request["scope"]
    relation_count = int(scope["relation_count"])
    relations = request["relations"]
    edges = request["edges"]
    computed_graph_hash = stable_graph_hash(relations, edges)
    if request["graph_hash"] != computed_graph_hash:
        raise SmokeError(
            f"Request graph_hash does not match relations/edges: "
            f"{request['graph_hash']} != {computed_graph_hash}"
        )
    if relation_count != len(relations):
        raise SmokeError(f"relation_count={relation_count} but relation rows={len(relations)}")
    relation_ids = [int(item["relation_id"]) for item in relations]
    if len(relation_ids) != len(set(relation_ids)):
        raise SmokeError("Request relation ids are not unique")
    relation_id_set = set(relation_ids)
    for edge in edges:
        if edge.get("join_type") != "INNER":
            raise SmokeError(f"Unsupported edge join_type: {edge.get('join_type')}")
        left = int(edge["left_relation_id"])
        right = int(edge["right_relation_id"])
        if left not in relation_id_set or right not in relation_id_set:
            raise SmokeError(f"Request edge references an unknown relation: {left}, {right}")
    if not edges and relation_count > 1:
        raise SmokeError("Request has more than one relation but no join edges")


class ZeroStateCost:
    def __init__(self, torch_module: Any):
        self.torch = torch_module

    def __call__(self, features: Any) -> Any:
        if features.dim() == 1:
            return self.torch.zeros(1, device=features.device)
        return self.torch.zeros((features.shape[0], 1), device=features.device)


class PreferHighRelationIdRemoval:
    def __init__(self, torch_module: Any, relation_count: int):
        self.torch = torch_module
        self.relation_count = relation_count

    def __call__(self, edge_features: Any) -> Any:
        half = edge_features.shape[1] // 2
        removed = edge_features[:, half:] - edge_features[:, :half]
        relation_part = removed[:, : self.relation_count]
        weights = self.torch.arange(
            self.relation_count, device=edge_features.device, dtype=edge_features.dtype
        ).view(-1, 1)
        return -(relation_part @ weights)


def build_query_for_neuso(request: dict[str, Any], deps: NeuSODependencies, device: str) -> Any:
    torch = deps.torch
    nx = deps.nx
    relation_ids = sorted(int(item["relation_id"]) for item in request["relations"])
    id_to_node = {relation_id: idx for idx, relation_id in enumerate(relation_ids)}
    graph = nx.Graph()
    graph.add_nodes_from(range(len(relation_ids)))
    for edge in request["edges"]:
        graph.add_edge(id_to_node[int(edge["left_relation_id"])], id_to_node[int(edge["right_relation_id"])])

    relation_by_id = {int(item["relation_id"]): item for item in request["relations"]}
    features = []
    for relation_id in relation_ids:
        relation = relation_by_id[relation_id]
        row = [0.0] * len(relation_ids)
        row[id_to_node[relation_id]] = 1.0
        estimated_cardinality = relation.get("estimated_cardinality") or relation.get("base_cardinality") or 0
        row.append(math.log1p(float(estimated_cardinality)))
        row.append(float(relation.get("degree", 0)))
        features.append(row)

    query = type("NeuSOQuery", (), {})()
    query.graph = graph
    query.node_feature = torch.tensor(features, dtype=torch.float32, device=device)
    query.relation_ids = relation_ids
    return query


def infer_join_order(request: dict[str, Any], deps: NeuSODependencies, device: str) -> tuple[list[int], float]:
    if device == "cuda" and not deps.torch.cuda.is_available():
        raise SmokeError("CUDA was requested but torch.cuda.is_available() is false")
    validate_request(request)
    query = build_query_for_neuso(request, deps, device)
    relation_count = len(query.relation_ids)
    enumerator = deps.plan_enumerator_cls(
        ZeroStateCost(deps.torch),
        ZeroStateCost(deps.torch),
        PreferHighRelationIdRemoval(deps.torch, relation_count),
    )
    start_time = time.perf_counter()
    node_order = enumerator.GenPlan(query)
    if device == "cuda":
        deps.torch.cuda.synchronize()
    latency_ms = (time.perf_counter() - start_time) * 1000
    relation_order = [query.relation_ids[node_id] for node_id in node_order]
    return relation_order, latency_ms


def edge_set(request: dict[str, Any]) -> set[tuple[int, int]]:
    result = set()
    for edge in request["edges"]:
        left = int(edge["left_relation_id"])
        right = int(edge["right_relation_id"])
        result.add((left, right))
        result.add((right, left))
    return result


def validate_response(request: dict[str, Any], response: dict[str, Any]) -> None:
    for field in ["version", "request_id", "graph_hash", "status", "model_version", "join_order", "latency_ms"]:
        if field not in response:
            raise SmokeError(f"Response is missing required field: {field}")
    if response["request_id"] != request["request_id"]:
        raise SmokeError("Response request_id does not match request")
    if response["graph_hash"] != request["graph_hash"]:
        raise SmokeError("Response graph_hash does not match request")
    if response["status"] != "ok":
        raise SmokeError(f"Response status is not ok: {response['status']}")
    relation_ids = {int(item["relation_id"]) for item in request["relations"]}
    join_order = [int(item) for item in response["join_order"]]
    if len(join_order) != len(relation_ids):
        raise SmokeError("join_order length does not match relation count")
    if set(join_order) != relation_ids:
        raise SmokeError("join_order is not a full relation-id permutation")
    if float(response["latency_ms"]) < 0:
        raise SmokeError("latency_ms must be non-negative")

    edges = edge_set(request)
    joined = {join_order[0]}
    for relation_id in join_order[1:]:
        if not any((relation_id, existing) in edges for existing in joined):
            raise SmokeError(f"join_order append is disconnected at relation {relation_id}")
        joined.add(relation_id)


def build_response(request: dict[str, Any], join_order: list[int], latency_ms: float) -> dict[str, Any]:
    return {
        "version": 1,
        "request_id": request["request_id"],
        "graph_hash": request["graph_hash"],
        "status": "ok",
        "model_version": MODEL_VERSION,
        "join_order": join_order,
        "latency_ms": round(latency_ms, 4),
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open() as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise SmokeError(f"Required regression file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SmokeError(f"Regression file is not valid JSON: {path}") from exc


def normalize_response(response: dict[str, Any]) -> dict[str, Any]:
    return {field: response[field] for field in STABLE_RESPONSE_FIELDS if field in response}


def response_diff(expected: dict[str, Any], actual: dict[str, Any], case_dir: Path) -> str:
    expected_text = canonical_json(expected).splitlines(keepends=True)
    actual_text = canonical_json(actual).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            expected_text,
            actual_text,
            fromfile=str(case_dir / "expected_response.json"),
            tofile=str(case_dir / "actual_response.normalized.json"),
        )
    )


def write_outputs(output_dir: Path | None, request: dict[str, Any], response: dict[str, Any]) -> None:
    if output_dir is None:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "neuso_request.json").open("w") as handle:
        json.dump(request, handle, indent=2)
        handle.write("\n")
    with (output_dir / "neuso_response.json").open("w") as handle:
        json.dump(response, handle, indent=2)
        handle.write("\n")


def write_regression_outputs(
    output_dir: Path | None,
    case_dir: Path,
    request: dict[str, Any],
    response: dict[str, Any],
    normalized_response: dict[str, Any],
) -> None:
    if output_dir is None:
        return
    case_output_dir = output_dir / case_dir.name
    case_output_dir.mkdir(parents=True, exist_ok=True)
    with (case_output_dir / "actual_request.json").open("w") as handle:
        json.dump(request, handle, indent=2)
        handle.write("\n")
    with (case_output_dir / "actual_response.json").open("w") as handle:
        json.dump(response, handle, indent=2)
        handle.write("\n")
    with (case_output_dir / "actual_response.normalized.json").open("w") as handle:
        json.dump(normalized_response, handle, indent=2)
        handle.write("\n")


def load_regression_case(case_dir: Path) -> RegressionCase:
    sql_path = case_dir / "input.sql"
    request_path = case_dir / "input_request.json"
    expected_path = case_dir / "expected_response.json"
    if not sql_path.exists():
        raise SmokeError(f"Required regression file does not exist: {sql_path}")
    sql = sql_path.read_text()
    request = read_json(request_path)
    expected_response = read_json(expected_path)
    return RegressionCase(case_dir=case_dir, sql=sql, request=request, expected_response=expected_response)


def discover_regression_cases(case_dir: Path | None, testdata_dir: Path | None) -> list[Path]:
    if case_dir is not None:
        return [case_dir]
    root = testdata_dir or DEFAULT_TESTDATA_DIR
    if not root.exists():
        raise SmokeError(f"Regression testdata directory does not exist: {root}")
    cases = sorted(
        item
        for item in root.iterdir()
        if item.is_dir() and (item / "input_request.json").exists() and (item / "expected_response.json").exists()
    )
    if not cases:
        raise SmokeError(f"No regression cases found under: {root}")
    return cases


def run_regression_case(
    case: RegressionCase,
    deps: NeuSODependencies,
    device: str,
    output_dir: Path | None,
) -> dict[str, Any]:
    if not case.sql.strip():
        raise SmokeError(f"Regression input.sql is empty: {case.case_dir / 'input.sql'}")
    join_order, latency_ms = infer_join_order(case.request, deps, device)
    response = build_response(case.request, join_order, latency_ms)
    validate_response(case.request, response)
    normalized_response = normalize_response(response)
    if normalized_response != case.expected_response:
        diff = response_diff(case.expected_response, normalized_response, case.case_dir)
        raise SmokeError(f"Regression response mismatch for case {case.case_dir.name}:\n{diff}")
    write_regression_outputs(output_dir, case.case_dir, case.request, response, normalized_response)
    return response


def run_regression(args: argparse.Namespace, deps: NeuSODependencies) -> int:
    case_dirs = discover_regression_cases(args.case_dir, args.testdata_dir)
    for case_dir in case_dirs:
        case = load_regression_case(case_dir)
        run_regression_case(case, deps, args.device, args.output)
        print(
            f"regression case: {case_dir.name} ok "
            f"(relations={case.request['scope']['relation_count']}, edges={len(case.request['edges'])})"
        )

    print("neuso_runtime_bridge_smoke: ok")
    print(f"mode: {args.mode}")
    print(f"device: {args.device}")
    print(f"case_count: {len(case_dirs)}")
    return 0


def run_smoke(args: argparse.Namespace) -> int:
    deps = load_neuso_dependencies()
    if args.mode in ("regression", "golden"):
        return run_regression(args, deps)
    if args.mode == "fixture":
        request = build_fixture_request()
    elif args.mode == "duckdb-export":
        if args.duckdb is None:
            raise SmokeError("--duckdb is required in duckdb-export mode")
        output_dir = args.output or Path("/tmp/neuso-runtime-bridge-smoke")
        database = args.database or output_dir / "neuso-runtime-smoke.duckdb"
        export = run_duckdb_export(args.duckdb, database, output_dir)
        request = adapt_duckdb_export(export)
    else:
        raise SmokeError(f"Unsupported mode: {args.mode}")

    join_order, latency_ms = infer_join_order(request, deps, args.device)
    response = build_response(request, join_order, latency_ms)
    validate_response(request, response)
    write_outputs(args.output, request, response)

    print("neuso_runtime_bridge_smoke: ok")
    print(f"mode: {args.mode}")
    print(f"device: {args.device}")
    print(f"relation_count: {request['scope']['relation_count']}")
    print(f"edge_count: {len(request['edges'])}")
    print("response:")
    print(json.dumps(response, indent=2))
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["regression", "golden", "fixture", "duckdb-export"], default="regression")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--case-dir", type=Path, help="Single regression case directory")
    parser.add_argument(
        "--testdata-dir",
        type=Path,
        default=DEFAULT_TESTDATA_DIR,
        help="Directory containing regression case subdirectories",
    )
    parser.add_argument("--duckdb", type=Path, help="DuckDB CLI binary for duckdb-export mode")
    parser.add_argument("--database", type=Path, help="DuckDB database path for duckdb-export mode")
    parser.add_argument("--output", type=Path, help="Directory to write smoke request/response JSON files")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        return run_smoke(args)
    except (SmokeError, subprocess.TimeoutExpired) as exc:
        print(f"neuso_runtime_bridge_smoke: failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
