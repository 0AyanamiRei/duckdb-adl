#!/usr/bin/env python3
"""HTTP sidecar for the experimental NeuSO runtime bridge."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from neuso_runtime_bridge_smoke import (
    SmokeError,
    build_response,
    infer_join_order,
    load_neuso_dependencies,
    stable_graph_hash,
)


class NeuSOSidecar:
    def __init__(self, device: str, trace_file: Path | None):
        self.device = device
        self.trace_file = trace_file
        self.deps = load_neuso_dependencies()
        self.lock = threading.Lock()

    def infer(self, request: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if "graph_hash" not in request:
                request["graph_hash"] = stable_graph_hash(request["relations"], request["edges"])
            join_order, latency_ms = infer_join_order(request, self.deps, self.device)
            response = build_response(request, join_order, latency_ms)
            if self.trace_file is not None:
                self.trace_file.parent.mkdir(parents=True, exist_ok=True)
                with self.trace_file.open("w") as handle:
                    json.dump({"request": request, "response": response}, handle, indent=2)
                    handle.write("\n")
            return response


class NeuSORequestHandler(BaseHTTPRequestHandler):
    server_version = "NeuSORuntimeSidecar/0.1"

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._write_json(HTTPStatus.NOT_FOUND, {"status": "not_found"})
            return
        self._write_json(HTTPStatus.OK, {"status": "ok", "model_version": "neuso_contract_smoke"})

    def do_POST(self) -> None:
        if self.path != "/infer_join_order":
            self._write_json(HTTPStatus.NOT_FOUND, {"status": "not_found"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(content_length).decode("utf-8"))
            response = self.server.sidecar.infer(request)  # type: ignore[attr-defined]
            self._write_json(HTTPStatus.OK, response)
        except (json.JSONDecodeError, SmokeError, KeyError, ValueError) as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": str(exc)})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"neuso_runtime_sidecar: {self.address_string()} - {fmt % args}", file=sys.stderr)


class NeuSOServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], sidecar: NeuSOSidecar):
        super().__init__(server_address, NeuSORequestHandler)
        self.sidecar = sidecar


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--trace-file", type=Path, help="Optional file to write the last runtime request/response")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    sidecar = NeuSOSidecar(args.device, args.trace_file)
    server = NeuSOServer((args.host, args.port), sidecar)
    print(f"neuso_runtime_sidecar: listening on http://{args.host}:{args.port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
