# 0001 ADL-OPT Harness Bootstrap

English TL;DR: Bootstrap the ADL-OPT documentation harness and define the offline TPC-H experiment contract without changing DuckDB behavior.

Updated: 2026-05-06

Key terms: bootstrap, TPC-H, offline harness, JSONL, plan control

## Goal

Create the first ADL-OPT research harness layer for this DuckDB checkout:

- Short agent map in `AGENTS.md`.
- DuckDB/ADL-OPT architecture map.
- Research design docs.
- Experiment artifact schema.
- Quality, reliability, and security criteria.

## Non-Goals

- No C++ optimizer changes.
- No DuckDB public API changes.
- No online model inference.
- No full benchmark execution in this bootstrap step.

## Implementation Tasks

- Replace `AGENTS.md` with a concise navigation file.
- Add root `ARCHITECTURE.md`.
- Add `docs/design-docs/` with core beliefs, NeuSO adaptation, DuckDB join-order notes, and feature/label schema.
- Add `docs/product-specs/adl-opt-research-spec.md`.
- Add `docs/references/` summaries for ADL-OPT, NeuSO, and related work.
- Add `docs/generated/` placeholders for optimizer map, TPC-H schema, and experiment artifacts.
- Add quality, reliability, security, and plan index docs.
- Add `scripts/adl_opt/offline_tpch_harness.py` as a minimal static/optional-execute TPC-H JSONL runner.

## Validation

- `AGENTS.md` links point to existing docs.
- Every ADL-OPT doc has English TL;DR, `Updated: 2026-05-06`, and key terms.
- `CLAUDE.md` remains unchanged.
- No tracked DuckDB source files are modified except the new documentation and `AGENTS.md`.
- Static runner mode generates the five required JSONL files for Q3/Q5/Q8 without requiring a DuckDB binary.

## Next Plan

After bootstrap, create a new execution plan for the first offline runner:

- Generate/load TPC-H SF 0.1 or SF 0.01.
- Extract Q3/Q5/Q8 join graphs.
- Generate default, original, heuristic, and random variants.
- Collect JSONL artifacts and summary metrics.

Dataset-size and benchmark-round defaults are defined in `docs/design-docs/experiment-scale-and-benchmark-protocol.md`.
