# Usage

This tool writes local, report-only M&A blacklist governance artifacts for a Robot Wealth pairs universe. It does not edit a live blacklist file.

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
ALPHA_VANTAGE_API_KEY=
ALPHA_VANTAGE_MAX_TICKERS=25
ALPHA_VANTAGE_REQUEST_INTERVAL_SECONDS=1.1
```

`OPENAI_REASONING_EFFORT` defaults to `medium`. Supported values are model-dependent; this repo accepts `none`, `minimal`, `low`, `medium`, `high`, and `xhigh`.

Rich mode uses explicit provider windows: Alpaca corporate actions fetch 60 days back and 60 days forward, then keep the 7-days-back / 30-days-forward operational window; Alpaca news uses a 7-day lookback with a 500-article paginated budget; Alpaca announcements use declaration dates over a 30-day lookback capped at 90 days; Alpha Vantage news is queried per ticker over a 7-day window with `limit=100`, a default 25-ticker request budget, and a default 1.1-second request interval.

## Preflight

```bash
ma-blacklist-governance preflight
```

Preflight fails when required lite credentials or the yfinance dependency are missing. Optional rich providers are shown as skipped when not configured.

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

## Public-Safe Fixtures

Committed fixtures must be synthetic. Do not commit real provider payloads, live blacklist state, real recommendation reports, API secrets, provider account identifiers, private logs, private local paths, or private strategy mechanics.
