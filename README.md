# M&A Blacklist Governance

An LLM-assisted M&A blacklist governance tool for Robot Wealth pairs traders: automatically discover affected tickers, gather M&A evidence including Alpaca corporate actions/news/announcements, and produce consistent promotion and exit-review reports without manual ticker-by-ticker first-pass review.

It replaces repetitive first-pass manual review with a structured pipeline: scan the Robot Wealth universe, gather M&A-relevant evidence, attach pair context, run an LLM governance review, and produce local ticker-level reports for promotion or exit candidates. Instead of manually checking every candidate name and durable blacklist entry, operators review a consistent artifact with saved evidence, source health, and the LLM's structured assessment.

The tool has two review modes. Promotion review starts from the current Robot Wealth universe, discovers tickers with M&A evidence, and sends only that candidate set through governance. Durable-block exit review starts from an existing blacklist file and reviews each active name for keep/remove consideration while preserving the input file unchanged.

The Alpaca side is built around the discovery gotchas found during operator testing: corporate-action defaults were too narrow, so the tool fetches a wider window before applying the operational window; corporate-action payloads are parsed by event shape instead of assuming one flat symbol field; announcements use declaration dates and role-specific target/acquirer mapping; and Alpaca news uses explicit lookback, chunking, and pagination so default/current-day behavior does not silently miss evidence.

## Product Scope

The intended product provides two purpose-framed workflows:

- Promotion review: discover and evaluate candidate tickers for possible durable blacklist promotion.
- Durable-block exit review: evaluate existing durable blacklist names for keep/remove recommendations.

The base setup is a lite workflow using Robot Wealth, yfinance news and market data, and OpenAI. Optional richer evidence can add providers such as Alpaca and Alpha Vantage when users configure them.

All v1 workflows are report-only. They must not edit a live blacklist file.

## Setup

Install locally:

```bash
python -m pip install -e .
```

Configure required lite credentials in an untracked `.env` or the shell:

```bash
ROBOT_WEALTH_API_KEY=...
OPENAI_API_KEY=...
```

Optional variables:

```bash
ROBOT_WEALTH_API_BASE_URL=https://api.robotwealth.com/v1
OPENAI_MODEL=gpt-5.5
OPENAI_REASONING_EFFORT=medium
MA_BLACKLIST_OUTPUT_ROOT=runs
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_MARKET_DATA_FEED=
ALPHA_VANTAGE_API_KEY=
ALPHA_VANTAGE_MAX_TICKERS=25
ALPHA_VANTAGE_REQUEST_INTERVAL_SECONDS=1.1
```

`OPENAI_REASONING_EFFORT` defaults to `medium`. Supported values are model-dependent; this repo accepts `none`, `minimal`, `low`, `medium`, `high`, and `xhigh`.

Optional rich evidence providers can be configured later. Missing optional providers are recorded as skipped evidence sources instead of blocking lite workflows. Rich mode uses explicit provider windows: Alpaca corporate actions fetch 60 days back and 60 days forward, then keep the 7-days-back / 30-days-forward operational window; Alpaca news uses a 7-day lookback with a 500-article paginated budget; Alpaca announcements use declaration dates over a 30-day lookback capped at 90 days; Alpha Vantage news is queried per ticker over a 7-day window with `limit=100`, a default 25-ticker request budget, and a default 1.1-second request interval.

Market evidence is NYSE-session aligned with `pandas-market-calendars`. yfinance and optional Alpaca market data attach compact Lite+ context to selected candidates only: latest close/volume, short-window returns, trailing volume/range, optional baseline, event-anchor reaction metrics, and SPY-adjusted returns when benchmark bars are available. Market context is sent to governance as corroborating evidence only and does not admit discovery candidates by itself.

`ALPACA_MARKET_DATA_FEED` is optional. Leave it blank for Alpaca defaults, or set it to a feed value supported by your Alpaca market-data subscription when troubleshooting market-data coverage.

## Preflight

```bash
ma-blacklist-governance preflight
```

Preflight fails when required lite credentials or the yfinance dependency are missing. Optional rich providers are shown as skipped when not configured.

## Commands

```bash
ma-blacklist-governance preflight
ma-blacklist-governance discover --output-root runs
ma-blacklist-governance promotion-review --output-root runs
ma-blacklist-governance durable-exit-review --blacklist-file path/to/tickers.txt --output-root runs
ma-blacklist-governance prune-runs --output-root runs --older-than-days 30
```

## Discovery

```bash
ma-blacklist-governance discover --output-root runs
```

Discovery fetches the Robot Wealth universe, derives unique ticker coverage and pair context, and writes deterministic candidate artifacts. It does not call OpenAI.

## Promotion Review

```bash
ma-blacklist-governance promotion-review --output-root runs
```

When no candidates are supplied, promotion review scans the current Robot Wealth universe first and runs governance only on the discovery set. If discovery is empty, it writes a no-candidates report and does not send the full universe to governance.

If you already have an active durable blacklist, pass it as state context so promotion review does not re-review existing durable names for promotion:

```bash
ma-blacklist-governance promotion-review \
  --durable-blacklist-file path/to/tickers.txt \
  --output-root runs
```

Synthetic fixture workflow from a fresh checkout:

```bash
PYTHONPATH=src python -m ma_blacklist_governance discover \
  --universe-json tests/fixtures/rw_universe.json \
  --news-json tests/fixtures/yfinance_news.json \
  --market-json tests/fixtures/yfinance_market.json \
  --output-root runs \
  --run-id fixture-discover

PYTHONPATH=src python -m ma_blacklist_governance promotion-review \
  --offline-fake-governance \
  --universe-json tests/fixtures/rw_universe.json \
  --news-json tests/fixtures/yfinance_news.json \
  --market-json tests/fixtures/yfinance_market.json \
  --output-root runs \
  --run-id fixture-promotion

PYTHONPATH=src python -m ma_blacklist_governance durable-exit-review \
  --offline-fake-governance \
  --blacklist-file tests/fixtures/durable_blacklist.txt \
  --universe-json tests/fixtures/rw_universe.json \
  --news-json tests/fixtures/yfinance_no_candidates.json \
  --market-json tests/fixtures/yfinance_market.json \
  --output-root runs \
  --run-id fixture-exit
```

Inspect the generated artifacts:

```bash
jq '[.[] | {ticker, input_status, evidence_count: (.evidence | length)}]' \
  runs/fixture-discover/discover/discovery.json

jq '[.[] | {ticker, input_status, recommendation, affected_pair_count}]' \
  runs/fixture-promotion/promotion-review/governance_results.json

jq '[.[] | {ticker, input_status, recommendation, affected_pair_count}]' \
  runs/fixture-exit/durable-exit-review/governance_results.json

sed -n '1,120p' runs/fixture-promotion/promotion-review/report.md
```

## Durable-Block Exit Review

```bash
ma-blacklist-governance durable-exit-review \
  --blacklist-file path/to/tickers.txt \
  --output-root runs
```

Every active non-comment ticker in the supplied file is reviewed as durable blocked. Out-of-universe names stay in scope with explicit missing pair context. The input file is checked after the run and must remain unchanged.

Synthetic fixture command:

```bash
PYTHONPATH=src python -m ma_blacklist_governance durable-exit-review \
  --offline-fake-governance \
  --blacklist-file tests/fixtures/durable_blacklist.txt \
  --universe-json tests/fixtures/rw_universe.json \
  --news-json tests/fixtures/yfinance_no_candidates.json \
  --market-json tests/fixtures/yfinance_market.json \
  --output-root runs \
  --run-id fixture-exit
```

## Retention

```bash
ma-blacklist-governance prune-runs --output-root runs --older-than-days 30
```

This deletes old local run directories only. It requires the tool-created run marker and does not touch input blacklist files or unrelated directories under the output root.

For the synthetic fixture workflow above:

```bash
PYTHONPATH=src python -m ma_blacklist_governance prune-runs --output-root runs --older-than-days 0
```

## Public Boundary

This repo is meant to be safe to share publicly. Public code, docs, examples, and tests must not include:

- API keys, account identifiers, private endpoints, private logs, or local absolute paths.
- Live blacklist state, real recommendation reports, retained provider artifacts, or real candidates.
- Private trading strategy mechanics, pair ranking or selection logic, sizing, execution, broker integration, or performance data.

Use synthetic or sanitized fixtures for examples and tests.

Local run artifacts under `runs/<run-id>/<workflow>/` are ignored by git. Treat generated reports as sensitive local trading-governance outputs unless you have reviewed and sanitized them. `prune-runs` only removes directories marked as this tool's run artifacts.

## OpenAI Data Boundary

Governance review sends compact saved evidence, structured market context, pair context, provider health, and input-status constraints to OpenAI. The request excludes secrets, private local paths, provider account identifiers, and raw retained provider payloads. The response uses strict JSON-schema structured output and is validated locally before report generation.
