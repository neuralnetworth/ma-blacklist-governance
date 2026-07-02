"""Optional rich evidence hooks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..config import Config
from ..models import EvidenceRecord, EvidenceStrength, ProviderHealth, ProviderState
from ..security import redact_text
from .yfinance_provider import MA_KEYWORDS


ALPACA_CORPORATE_ACTIONS_URL = "https://data.alpaca.markets/v1/corporate-actions"
ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
ALPACA_BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
ALPACA_MA_ACTION_TYPES = "cash_merger,stock_merger,stock_and_cash_merger,reorganization,name_change,spin_off"
MAX_SYMBOLS_PER_REQUEST = 50
MAX_PROVIDER_PAGES = 3


def _record_from_payload(payload: dict[str, Any]) -> EvidenceRecord:
    strength = payload.get("strength")
    if not strength:
        strength = EvidenceStrength.STRUCTURED if payload.get("source_type") in {"corporate_action", "announcement"} else EvidenceStrength.NEWS
    return EvidenceRecord(
        ticker=str(payload["ticker"]).upper(),
        provider=str(payload.get("provider", "rich")),
        source_type=str(payload.get("source_type", "rich_evidence")),
        strength=strength,
        source_date=payload.get("source_date"),
        title=payload.get("title"),
        summary=payload.get("summary"),
        url=payload.get("url"),
        role_hint=payload.get("role_hint"),
        metadata=dict(payload.get("metadata") or {}),
    )


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _json_get(url: str, params: dict[str, Any], headers: dict[str, str] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    request = Request(f"{url}?{query}", headers=headers or {}, method="GET")
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("provider response was not a JSON object")
    return payload


def _payload_items(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    if isinstance(payload.get("data"), list):
        return [item for item in payload["data"] if isinstance(item, dict)]
    return []


def _matches_ma_text(*values: Any) -> bool:
    haystack = " ".join(str(value or "") for value in values).lower()
    return any(keyword in haystack for keyword in MA_KEYWORDS)


def _alpaca_headers(config: Config) -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": config.alpaca_api_key or "",
        "APCA-API-SECRET-KEY": config.alpaca_secret_key or "",
    }


def _alpaca_corporate_actions(config: Config, tickers: list[str]) -> tuple[list[EvidenceRecord], ProviderHealth]:
    records: list[EvidenceRecord] = []
    pages = 0
    for chunk in _chunks(tickers, MAX_SYMBOLS_PER_REQUEST):
        page_token: str | None = None
        while pages < MAX_PROVIDER_PAGES:
            payload = _json_get(
                ALPACA_CORPORATE_ACTIONS_URL,
                {
                    "symbols": ",".join(chunk),
                    "types": ALPACA_MA_ACTION_TYPES,
                    "region": "us",
                    "limit": 1000,
                    "page_token": page_token,
                },
                _alpaca_headers(config),
            )
            pages += 1
            for item in _payload_items(payload, "corporate_actions", "corporate_actions_v2", "data"):
                ticker = str(item.get("symbol") or item.get("old_symbol") or item.get("new_symbol") or "").upper()
                if ticker not in tickers:
                    continue
                action_type = str(item.get("type") or "corporate_action")
                records.append(
                    EvidenceRecord(
                        ticker=ticker,
                        provider="alpaca_corporate_actions",
                        source_type="corporate_action",
                        strength=EvidenceStrength.STRUCTURED,
                        source_date=item.get("process_date") or item.get("ex_date") or item.get("record_date"),
                        title=f"Alpaca corporate action: {action_type}",
                        summary=json.dumps(item, sort_keys=True),
                        metadata={"action_type": action_type, "raw": item},
                    )
                )
            page_token = payload.get("next_page_token")
            if not page_token:
                break
    return records, ProviderHealth(provider="alpaca_corporate_actions", state=ProviderState.OK, records=len(records), message=f"{len(records)} records")


def _alpaca_news(config: Config, tickers: list[str]) -> tuple[list[EvidenceRecord], ProviderHealth]:
    records: list[EvidenceRecord] = []
    pages = 0
    for chunk in _chunks(tickers, MAX_SYMBOLS_PER_REQUEST):
        page_token: str | None = None
        while pages < MAX_PROVIDER_PAGES:
            payload = _json_get(
                ALPACA_NEWS_URL,
                {
                    "symbols": ",".join(chunk),
                    "limit": 50,
                    "include_content": "true",
                    "sort": "desc",
                    "page_token": page_token,
                },
                _alpaca_headers(config),
            )
            pages += 1
            for item in _payload_items(payload, "news"):
                symbols = [str(symbol).upper() for symbol in item.get("symbols", [])]
                title = item.get("headline") or item.get("title")
                summary = item.get("summary") or item.get("content")
                if not _matches_ma_text(title, summary):
                    continue
                for ticker in symbols:
                    if ticker not in tickers:
                        continue
                    records.append(
                        EvidenceRecord(
                            ticker=ticker,
                            provider="alpaca_news",
                            source_type="news",
                            strength=EvidenceStrength.NEWS,
                            source_date=item.get("created_at") or item.get("updated_at"),
                            title=title,
                            summary=summary,
                            url=item.get("url"),
                            metadata={"article_id": item.get("id")},
                        )
                    )
            page_token = payload.get("next_page_token")
            if not page_token:
                break
    return records, ProviderHealth(provider="alpaca_news", state=ProviderState.OK, records=len(records), message=f"{len(records)} records")


def _alpha_vantage_news(config: Config, tickers: list[str]) -> tuple[list[EvidenceRecord], ProviderHealth]:
    if not tickers:
        return [], ProviderHealth(provider="alpha_vantage", state=ProviderState.SKIPPED, message="no tickers supplied")
    payload = _json_get(
        ALPHA_VANTAGE_URL,
        {
            "function": "NEWS_SENTIMENT",
            "tickers": ",".join(tickers[:MAX_SYMBOLS_PER_REQUEST]),
            "topics": "mergers_and_acquisitions",
            "sort": "LATEST",
            "limit": 50,
            "apikey": config.alpha_vantage_api_key,
        },
    )
    records: list[EvidenceRecord] = []
    for item in _payload_items(payload, "feed"):
        title = item.get("title")
        summary = item.get("summary")
        ticker_sentiment = item.get("ticker_sentiment") or []
        symbols = [str(entry.get("ticker")).upper() for entry in ticker_sentiment if isinstance(entry, dict) and entry.get("ticker")]
        if not symbols:
            symbols = [ticker for ticker in tickers if ticker in f"{title} {summary}".upper()]
        for ticker in symbols:
            if ticker not in tickers:
                continue
            records.append(
                EvidenceRecord(
                    ticker=ticker,
                    provider="alpha_vantage",
                    source_type="news_sentiment",
                    strength=EvidenceStrength.NEWS,
                    source_date=item.get("time_published"),
                    title=title,
                    summary=summary,
                    url=item.get("url"),
                    metadata={"topics": item.get("topics"), "overall_sentiment_label": item.get("overall_sentiment_label")},
                )
            )
    return records, ProviderHealth(provider="alpha_vantage", state=ProviderState.OK, records=len(records), message=f"{len(records)} records")


def collect_alpaca_market_evidence(config: Config, tickers: list[str]) -> tuple[list[EvidenceRecord], ProviderHealth]:
    requested = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
    if not requested:
        return [], ProviderHealth(provider="alpaca_market_data", state=ProviderState.SKIPPED, message="no selected tickers")
    if not (config.alpaca_api_key and config.alpaca_secret_key):
        return [], ProviderHealth(provider="alpaca_market_data", state=ProviderState.SKIPPED, message="optional provider not configured")
    records: list[EvidenceRecord] = []
    try:
        for chunk in _chunks(requested, MAX_SYMBOLS_PER_REQUEST):
            payload = _json_get(
                ALPACA_BARS_URL,
                {
                    "symbols": ",".join(chunk),
                    "timeframe": "1Day",
                    "limit": 1000,
                    "adjustment": "all",
                },
                _alpaca_headers(config),
            )
            bars = payload.get("bars") if isinstance(payload.get("bars"), dict) else {}
            for ticker, rows in bars.items():
                row_list = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
                if not row_list:
                    continue
                first = row_list[0]
                latest = row_list[-1]
                first_close = first.get("c")
                latest_close = latest.get("c")
                metadata: dict[str, Any] = {
                    "first_close": first_close,
                    "latest_close": latest_close,
                    "latest_volume": latest.get("v"),
                    "rows": len(row_list),
                }
                if latest_close is not None and first_close not in (None, 0):
                    metadata["window_return"] = (float(latest_close) / float(first_close)) - 1.0
                records.append(
                    EvidenceRecord(
                        ticker=str(ticker).upper(),
                        provider="alpaca_market_data",
                        source_type="market_data",
                        strength=EvidenceStrength.MARKET,
                        source_date=latest.get("t"),
                        summary=(
                            "Alpaca market snapshot: "
                            f"latest_close={latest_close}, latest_volume={latest.get('v')}, rows={len(row_list)}, "
                            f"window_return={metadata.get('window_return')}"
                        ),
                        metadata=metadata,
                    )
                )
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return [], ProviderHealth(provider="alpaca_market_data", state=ProviderState.ERROR, message=redact_text(repr(exc), config.secret_values()))
    return records, ProviderHealth(provider="alpaca_market_data", state=ProviderState.OK, records=len(records), message=f"{len(records)} records")


def collect_rich_evidence(
    config: Config,
    fixture_path: Path | None = None,
    tickers: list[str] | None = None,
) -> tuple[list[EvidenceRecord], list[ProviderHealth]]:
    health: list[ProviderHealth] = []
    requested = sorted({ticker.strip().upper() for ticker in tickers or [] if ticker.strip()})
    if fixture_path:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        records = [_record_from_payload(item) for item in data.get("evidence", [])]
        if requested:
            records = [record for record in records if record.ticker in requested]
        providers = sorted({record.provider for record in records}) or ["rich_fixture"]
        for provider in providers:
            count = sum(1 for record in records if record.provider == provider)
            health.append(ProviderHealth(provider=provider, state=ProviderState.OK, records=count, message=f"{count} records"))
        return records, health

    records: list[EvidenceRecord] = []
    alpaca_configured = bool(config.alpaca_api_key and config.alpaca_secret_key)
    alpha_configured = bool(config.alpha_vantage_api_key)
    if alpaca_configured and requested:
        try:
            action_records, action_health = _alpaca_corporate_actions(config, requested)
            news_records, news_health = _alpaca_news(config, requested)
            records.extend([*action_records, *news_records])
            health.extend([action_health, news_health])
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            health.append(ProviderHealth(provider="alpaca", state=ProviderState.ERROR, message=redact_text(repr(exc), config.secret_values())))
    else:
        message = "no tickers supplied" if alpaca_configured else "optional provider not configured"
        health.append(ProviderHealth(provider="alpaca", state=ProviderState.SKIPPED, message=message))

    if alpha_configured and requested:
        try:
            alpha_records, alpha_health = _alpha_vantage_news(config, requested)
            records.extend(alpha_records)
            health.append(alpha_health)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            health.append(ProviderHealth(provider="alpha_vantage", state=ProviderState.ERROR, message=redact_text(repr(exc), config.secret_values())))
    else:
        message = "no tickers supplied" if alpha_configured else "optional provider not configured"
        health.append(ProviderHealth(provider="alpha_vantage", state=ProviderState.SKIPPED, message=message))

    return records, health
