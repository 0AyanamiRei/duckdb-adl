# ADL-OPT Plans

English TL;DR: This is the index of ADL-OPT execution plans. Active plans are live work; completed plans record what actually happened.

Updated: 2026-05-07

Key terms: execution plan, active plan, completed plan, tech debt, harness

## Active

- `docs/exec-plans/active/0001-adl-opt-harness-bootstrap.md`: bootstrap the documentation and offline experiment harness specification.
- `docs/exec-plans/active/0002-r1-tpch-execution-loop.md`: implement the SF0.01 TPC-H R1 executable loop, including data reuse, fixed join-order execution, correctness checks, latency/profile collection, and summary reporting.
- `docs/exec-plans/active/0003-large-join-endpoint-harness.md`: narrow ADL-OPT to n>12 large joins and add a static JOB/IMDB endpoint-append harness.
- `docs/exec-plans/active/0004-r5-ikkbz-linearization-export.md`: add DuckDB kernel export-only IKKBZ/MST large-join linearization metadata.

## Completed

No completed ADL-OPT execution plans yet.

## Tech Debt

Track durable research and implementation debt in `docs/exec-plans/tech-debt-tracker.md`.

## Experiment Protocol

- `docs/design-docs/experiment-scale-and-benchmark-protocol.md`: staged dataset sizes, benchmark rounds, resource budgets, correctness gates, and reporting requirements.
- `docs/design-docs/ikkbz-linearization-export-usage.md`: R5 debug settings, usage, JSON interpretation, and smoke/boundary tests for IKKBZ linearization export.

## Collaboration Protocol

- `docs/design-docs/pr-flow.md`: feature branch, pull request, upstream sync, and empty PR validation flow for the ADL-OPT fork-as-primary-project setup.

## Plan Rules

- Every active plan should name its goal, non-goals, inputs, outputs, validation, and rollback or pause criteria.
- Completed plans should summarize the implementation delta and link generated artifacts.
- Keep plan files concise enough that another agent can execute them without rereading every design document.
