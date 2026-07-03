# M&A Blacklist Governance

Standalone, report-only M&A blacklist governance for Robot Wealth pairs-universe operators.

This repository is a public-safe Apache-2.0 Python package for local, report-only M&A blacklist governance. It is designed for Robot Wealth pairs-universe operators who want evidence-backed review artifacts without sharing or coupling to a private trading repo.

## Product Scope

The intended product provides two purpose-framed workflows:

- Promotion review: discover and evaluate candidate tickers for possible durable blacklist promotion.
- Durable-block exit review: evaluate existing durable blacklist names for keep/remove recommendations.

The base setup is a lite workflow using Robot Wealth, yfinance news and market data, and OpenAI. Optional richer evidence can add providers such as Alpaca and Alpha Vantage when users configure them.

All v1 workflows are report-only. They must not edit a live blacklist file.

## Install

```bash
python -m pip install -e .
```

Create a local `.env` from `.env.example` or export the variables directly:

```bash
ROBOT_WEALTH_API_KEY=...
OPENAI_API_KEY=...
```

Optional rich evidence providers can be configured later. Missing optional providers are recorded as skipped evidence sources instead of blocking lite workflows.

## Commands

```bash
ma-blacklist-governance preflight
ma-blacklist-governance discover --output-root runs
ma-blacklist-governance promotion-review --output-root runs
ma-blacklist-governance durable-exit-review --blacklist-file path/to/tickers.txt --output-root runs
ma-blacklist-governance prune-runs --output-root runs --older-than-days 30
```

`promotion-review` implements first-run behavior: it scans the Robot Wealth universe, performs deterministic discovery, and sends only discovered candidates to governance. If discovery finds no candidates, it writes a no-candidates report and does not run governance over the full universe.

For offline examples and tests, the CLI accepts synthetic fixture inputs such as `--universe-json`, `--news-json`, `--market-json`, `--rich-evidence-json`, and `--offline-fake-governance`.

## Public Boundary

This repo is meant to be safe to share publicly. Public code, docs, examples, and tests must not include:

- API keys, account identifiers, private endpoints, private logs, or local absolute paths.
- Live blacklist state, real recommendation reports, retained provider artifacts, or real candidates.
- Private trading strategy mechanics, pair ranking or selection logic, sizing, execution, broker integration, or performance data.

Use synthetic or sanitized fixtures for examples and tests.

Local run artifacts under `runs/<run-id>/<workflow>/` are ignored by git. Treat generated reports as sensitive local trading-governance outputs unless you have reviewed and sanitized them. `prune-runs` only removes directories marked as this tool's run artifacts.

## OpenAI Data Boundary

Governance review sends compact saved evidence, pair context, provider health, and input-status constraints to OpenAI. The request excludes secrets, private local paths, provider account identifiers, and raw retained provider payloads. The response uses strict JSON-schema structured output and is validated locally before report generation.

## Development Handoff

The detailed planning artifact is maintained outside this public repository. Public development context should come from this README, `docs/usage.md`, tests, and any local-only handoff files the operator provides.

Local Codex handoff prompts can live under `.codex-local/` and are intentionally ignored by git:

- `.codex-local/ce-plan-input.md`
- `.codex-local/ce-lfg-input.md`
