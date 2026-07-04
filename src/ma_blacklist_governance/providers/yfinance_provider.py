"""Lite yfinance evidence provider."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..market_context import build_market_context, market_data_window, normalize_market_rows
from ..models import EvidenceRecord, EvidenceStrength, ProviderHealth, ProviderState


MA_KEYWORDS = {
    "merger",
    "acquisition",
    "acquire",
    "acquired",
    "takeover",
    "buyout",
    "going private",
    "definitive agreement",
    "tender offer",
    "exchange offer",
    "delist",
    "liquidation",
    "wind down",
    "bankruptcy",
    "restructuring",
    "spin-off",
    "spinoff",
}


def load_news_fixture(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("yfinance fixture must be an object keyed by ticker")
    return {str(k).upper(): list(v) for k, v in data.items()}


def load_market_fixture(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("market fixture must be an object keyed by ticker")
    return {str(k).upper(): v for k, v in data.items()}


def _article_field(article: dict[str, Any], *names: str) -> str | None:
    content = article.get("content") if isinstance(article.get("content"), dict) else {}
    for name in names:
        value = article.get(name)
        if value is None and content:
            value = content.get(name)
        if isinstance(value, dict) and "url" in value:
            value = value["url"]
        if value:
            return str(value)
    return None


def _matches(text: str) -> list[str]:
    haystack = text.lower()
    return sorted(keyword for keyword in MA_KEYWORDS if keyword in haystack)


def collect_news_evidence(
    tickers: list[str],
    news_by_ticker: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[list[EvidenceRecord], ProviderHealth]:
    evidence: list[EvidenceRecord] = []
    news = news_by_ticker or {}
    failed_tickers: list[str] = []
    if news_by_ticker is None:
        try:
            import yfinance as yf
        except Exception as exc:  # pragma: no cover - live provider path
            return [], ProviderHealth(provider="yfinance_news", state=ProviderState.ERROR, message=repr(exc))
        for ticker in tickers:
            try:
                news[ticker] = list(getattr(yf.Ticker(ticker), "news", []) or [])
            except Exception:
                failed_tickers.append(ticker)

    total = 0
    for ticker in tickers:
        for article in news.get(ticker.upper(), []):
            total += 1
            title = _article_field(article, "title") or ""
            summary = _article_field(article, "summary", "description") or ""
            matched = _matches(f"{title} {summary}")
            if not matched:
                continue
            published = _article_field(article, "published_at", "pubDate", "displayTime")
            url = _article_field(article, "url", "canonicalUrl", "clickThroughUrl")
            evidence.append(
                EvidenceRecord(
                    ticker=ticker.upper(),
                    provider="yfinance",
                    source_type="news",
                    strength=EvidenceStrength.NEWS,
                    source_date=published,
                    title=title,
                    summary=summary,
                    url=url,
                    metadata={"matched_keywords": matched},
                )
            )

    message = f"{len(evidence)} candidate articles"
    state = ProviderState.OK
    if failed_tickers:
        state = ProviderState.ERROR
        message = f"{message}; failed tickers: {', '.join(failed_tickers)}"
    return evidence, ProviderHealth(provider="yfinance_news", state=state, records=total, message=message)


def _market_record_from_payload(
    ticker: str,
    payload: Any,
    *,
    benchmark_payload: Any | None = None,
    event_dates: list[str] | None = None,
) -> EvidenceRecord | None:
    context = build_market_context(ticker, payload, benchmark_rows=benchmark_payload, event_dates=event_dates)
    if not context:
        return None
    bars, _malformed = normalize_market_rows(payload)
    first_close = bars[0].close if bars else None
    latest = context["latest"]
    returns = context["returns"]
    latest_close = latest["close"]
    latest_volume = latest["volume"]
    latest_date = latest["date"]
    window_return = returns.get("return_5_session")
    if window_return is None:
        window_return = returns.get("return_since_first_available")

    metadata: dict[str, Any] = {
        "latest_close": latest_close,
        "first_close": first_close,
        "latest_volume": latest_volume,
        "rows": context["coverage"]["rows"],
        "window_return": window_return,
        "market_context": context,
    }
    return EvidenceRecord(
        ticker=ticker.upper(),
        provider="yfinance",
        source_type="market_data",
        strength=EvidenceStrength.MARKET,
        source_date=str(latest_date) if latest_date else None,
        summary=(
            "Recent market context: "
            f"latest_close={latest_close}, latest_volume={latest_volume}, rows={metadata['rows']}, "
            f"return_5_session={returns.get('return_5_session')}, return_20_session={returns.get('return_20_session')}"
        ),
        metadata=metadata,
    )


def _history_to_payload(history: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in history.iterrows():
        rows.append(
            {
                "date": str(getattr(index, "date", lambda: index)()),
                "open": row.get("Open"),
                "high": row.get("High"),
                "low": row.get("Low"),
                "close": row.get("Close"),
                "volume": row.get("Volume"),
            }
        )
    return rows


def collect_market_evidence(
    tickers: list[str],
    market_by_ticker: dict[str, Any] | None = None,
    event_dates_by_ticker: dict[str, list[str]] | None = None,
) -> tuple[list[EvidenceRecord], ProviderHealth]:
    if not tickers:
        return [], ProviderHealth(provider="yfinance_market", state=ProviderState.SKIPPED, message="no selected tickers")
    live_ticker_errors: list[str] = []
    benchmark_state = "missing"
    benchmark_error: str | None = None
    if market_by_ticker is None:
        try:
            import yfinance as yf
        except Exception as exc:  # pragma: no cover - live provider path
            return [], ProviderHealth(provider="yfinance_market", state=ProviderState.ERROR, message=repr(exc))

        window = market_data_window()
        start = window.start.isoformat()
        end = (window.end + timedelta(days=1)).isoformat()
        market_by_ticker = {}
        for ticker in tickers:
            symbol = ticker.upper()
            try:
                market_by_ticker[symbol] = _history_to_payload(yf.Ticker(symbol).history(start=start, end=end, interval="1d"))
            except Exception as exc:  # pragma: no cover - live provider path
                live_ticker_errors.append(f"{symbol}: {exc!r}")
        try:
            market_by_ticker["SPY"] = _history_to_payload(yf.Ticker("SPY").history(start=start, end=end, interval="1d"))
            benchmark_state = "ok"
        except Exception as exc:  # pragma: no cover - live provider path
            benchmark_state = "error"
            benchmark_error = repr(exc)
    elif market_by_ticker.get("SPY") is not None:
        benchmark_state = "ok"

    evidence: list[EvidenceRecord] = []
    benchmark_payload = market_by_ticker.get("SPY")
    for ticker in tickers:
        payload = market_by_ticker.get(ticker.upper())
        if payload is None:
            continue
        record = _market_record_from_payload(
            ticker,
            payload,
            benchmark_payload=benchmark_payload,
            event_dates=(event_dates_by_ticker or {}).get(ticker.upper()),
        )
        if record:
            evidence.append(record)
    state = ProviderState.OK
    message = f"{len(evidence)} market snapshots; benchmark=SPY:{benchmark_state}"
    if live_ticker_errors:
        message = f"{message}; failed_tickers={len(live_ticker_errors)}"
    if benchmark_error:
        message = f"{message}; benchmark_error={benchmark_error}"
    if not evidence and live_ticker_errors:
        state = ProviderState.ERROR
    return evidence, ProviderHealth(provider="yfinance_market", state=state, records=len(evidence), message=message)
