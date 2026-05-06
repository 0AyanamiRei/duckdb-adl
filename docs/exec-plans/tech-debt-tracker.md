# ADL-OPT Tech Debt Tracker

English TL;DR: Track research and harness debt explicitly so future agents do not rediscover the same uncertainty.

Updated: 2026-05-06

Key terms: tech debt, uncertainty, experiment debt, implementation debt

## Open Debt

| ID | Area | Debt | Owner | Status |
| --- | --- | --- | --- | --- |
| TD-001 | Plan control | Need empirical proof that parenthesized joins plus `disabled_optimizers='join_order'` preserve intended join tree for selected TPC-H queries | ADL-OPT harness | Open |
| TD-002 | SQL parsing | Need decide whether v0 uses a lightweight SQL parser, DuckDB plan inspection, or manually curated TPC-H join graphs | ADL-OPT harness | Open |
| TD-003 | Cardinality labels | Need choose actual cardinality measurement strategy for intermediate states | ADL-OPT harness | Open |
| TD-004 | Model interface | Need define first PyTorch ranker/comparator once JSONL artifacts exist | ADL-OPT model | Open |
| TD-005 | Generated artifacts | Need add `.gitignore` policy for large generated DBs/checkpoints if experiment runner is added | ADL-OPT harness | Open |

## Debt Rules

- Add debt when implementation makes a simplifying assumption that affects research conclusions.
- Close debt only with a link to evidence, code, or generated artifact.
- Prefer small, named debts over vague warnings in prose.
