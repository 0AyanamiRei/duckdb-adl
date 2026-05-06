# ADL-OPT Security

English TL;DR: ADL-OPT v0 is an offline local research harness. Keep generated datasets, profiling output, and model artifacts out of accidental publication unless reviewed.

Updated: 2026-05-06

Key terms: security, local data, benchmark data, model artifact, generated output

## Scope

This document covers ADL-OPT research artifacts under `docs/` and future experiment outputs. The DuckDB project security policy remains in the root `SECURITY.md`.

## Data Handling

- TPC-H generated data is synthetic benchmark data, but generated databases can be large and should not be committed.
- Profiling output may include SQL text and local paths; review before sharing.
- Future JOB/IMDB/STATS-CEB data may have separate licenses and should be documented before use.
- Model checkpoints and feature dumps should be treated as generated artifacts and kept out of source control unless intentionally curated.

## Commands and Isolation

- Prefer local DuckDB binaries and local benchmark data.
- Do not add network-dependent experiment steps to v0 unless an execution plan records the dependency.
- Do not store credentials in JSONL artifacts, SQL files, generated docs, or model configs.

## Publication Checklist

Before sharing results:

- Confirm every included query and dataset license allows publication.
- Remove local absolute paths if they reveal private workspace details.
- Include DuckDB commit/build metadata for reproducibility.
- Include failures and exclusions, not only favorable runs.
