# M&A Blacklist Governance

Standalone, report-only M&A blacklist governance for Robot Wealth pairs-universe operators.

This repository is scaffolded for a public Apache-2.0 build. It is not an implementation yet. The next step is to run a Compound Engineering planning pass from this repo and turn the requirements in `docs/plans/brainstorm/` into an implementation-ready plan.

## Product Scope

The intended product provides two purpose-framed workflows:

- Promotion review: discover and evaluate candidate tickers for possible durable blacklist promotion.
- Durable-block exit review: evaluate existing durable blacklist names for keep/remove recommendations.

The base setup is a lite workflow using Robot Wealth, yfinance news and market data, and OpenAI. Optional richer evidence can add providers such as Alpaca and Alpha Vantage when users configure them.

All v1 workflows are report-only. They must not edit a live blacklist file.

## Public Boundary

This repo is meant to be safe to share publicly. Public code, docs, examples, and tests must not include:

- API keys, account identifiers, private endpoints, private logs, or local absolute paths.
- Live blacklist state, real recommendation reports, retained provider artifacts, or real candidates.
- Private trading strategy mechanics, pair ranking or selection logic, sizing, execution, broker integration, or performance data.

Use synthetic or sanitized fixtures for examples and tests.

## Development Handoff

Requirements live at:

- `docs/plans/brainstorm/2026-07-01-001-feat-ma-blacklist-governance-repo-plan.md`

Local Codex handoff prompts live under `.codex-local/` and are intentionally ignored by git:

- `.codex-local/ce-plan-input.md`
- `.codex-local/ce-lfg-input.md`

Open a Codex session in this repo and point it at one of those local handoff files.
