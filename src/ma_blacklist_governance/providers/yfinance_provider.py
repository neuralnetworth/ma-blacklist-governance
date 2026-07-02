"""Lite yfinance evidence provider."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def _market_record_from_payload(ticker: str, payload: Any) -> EvidenceRecord | None:
    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
        if not rows:
            return None
        first = rows[0]
        latest = rows[-1]
    elif isinstance(payload, dict):
        first = payload
        latest = payload
    else:
        return None

    latest_close = latest.get("close") or latest.get("Close")
    first_close = first.get("close") or first.get("Close")
    latest_volume = latest.get("volume") or latest.get("Volume")
    latest_date = latest.get("date") or latest.get("Date") or latest.get("timestamp")
    metadata: dict[str, Any] = {
        "latest_close": latest_close,
        "first_close": first_close,
        "latest_volume": latest_volume,
        "rows": len(payload) if isinstance(payload, list) else 1,
    }
    if latest_close is not None and first_close not in (None, 0):
        try:
            metadata["window_return"] = (float(latest_close) / float(first_close)) - 1.0
        except (TypeError, ValueError, ZeroDivisionError):
            metadata["window_return"] = None
    return EvidenceRecord(
        ticker=ticker.upper(),
        provider="yfinance",
        source_type="market_data",
        strength=EvidenceStrength.MARKET,
        source_date=str(latest_date) if latest_date else None,
        summary=(
            "Recent market snapshot: "
            f"latest_close={latest_close}, latest_volume={latest_volume}, rows={metadata['rows']}, "
            f"window_return={metadata.get('window_return')}"
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


def collect_market_evidence(tickers: list[str], market_by_ticker: dict[str, Any] | None = None) -> tuple[list[EvidenceRecord], ProviderHealth]:
    if not tickers:
        return [], ProviderHealth(provider="yfinance_market", state=ProviderState.SKIPPED, message="no selected tickers")
    if market_by_ticker is None:
        try:
            import yfinance as yf

            market_by_ticker = {
                ticker.upper(): _history_to_payload(yf.Ticker(ticker).history(period="5d", interval="1d"))
                for ticker in tickers
            }
        except Exception as exc:  # pragma: no cover - live provider path
            return [], ProviderHealth(provider="yfinance_market", state=ProviderState.ERROR, message=repr(exc))

    evidence: list[EvidenceRecord] = []
    for ticker in tickers:
        payload = market_by_ticker.get(ticker.upper())
        if payload is None:
            continue
        record = _market_record_from_payload(ticker, payload)
        if record:
            evidence.append(record)
    return evidence, ProviderHealth(provider="yfinance_market", state=ProviderState.OK, records=len(evidence), message=f"{len(evidence)} market snapshots")
