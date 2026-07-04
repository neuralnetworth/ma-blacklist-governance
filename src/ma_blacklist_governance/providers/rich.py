"""Optional rich evidence hooks."""

from __future__ import annotations

import json
import time as time_module
import warnings
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..config import Config
from ..market_context import build_market_context, market_data_window, normalize_market_rows
from ..models import EvidenceRecord, EvidenceStrength, ProviderHealth, ProviderState
from ..security import redact_text
from .yfinance_provider import MA_KEYWORDS


ALPACA_CORPORATE_ACTIONS_URL = "https://data.alpaca.markets/v1/corporate-actions"
ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
MAX_SYMBOLS_PER_REQUEST = 50
MAX_PROVIDER_PAGES = 3
ALPACA_CORP_API_LOOKBACK_DAYS = 60
ALPACA_CORP_API_LOOKAHEAD_DAYS = 60
ALPACA_CORP_OPERATIONAL_LOOKBACK_DAYS = 7
ALPACA_CORP_OPERATIONAL_LOOKAHEAD_DAYS = 30
ALPACA_NEWS_LOOKBACK_DAYS = 7
ALPACA_NEWS_MAX_ARTICLES = 500
ALPACA_NEWS_PAGE_LIMIT = 50
ALPACA_NEWS_MAX_PAGES = ALPACA_NEWS_MAX_ARTICLES // ALPACA_NEWS_PAGE_LIMIT
ALPACA_ANNOUNCEMENTS_LOOKBACK_DAYS = 30
ALPACA_ANNOUNCEMENTS_MAX_LOOKBACK_DAYS = 90
ALPHA_VANTAGE_NEWS_LOOKBACK_DAYS = 7
ALPHA_VANTAGE_NEWS_LIMIT = 100

CORPORATE_ACTION_EVENT_FIELDS: dict[str, tuple[str, str | None, str, str]] = {
    "cash_mergers": ("acquiree_symbol", "acquirer_symbol", "effective_date", "target/acquiree"),
    "stock_mergers": ("acquiree_symbol", "acquirer_symbol", "effective_date", "target/acquiree"),
    "stock_and_cash_mergers": ("acquiree_symbol", "acquirer_symbol", "effective_date", "target/acquiree"),
    "spin_offs": ("source_symbol", None, "ex_date", "source/security"),
    "name_changes": ("old_symbol", None, "process_date", "renamed/security"),
    "worthless_removals": ("symbol", None, "process_date", "affected/security"),
    "redemptions": ("symbol", None, "process_date", "affected/security"),
}
CORPORATE_ACTION_EVENT_ALIASES = {
    "cash_merger": "cash_mergers",
    "stock_merger": "stock_mergers",
    "stock_and_cash_merger": "stock_and_cash_mergers",
    "spin_off": "spin_offs",
    "name_change": "name_changes",
    "worthless_removal": "worthless_removals",
    "redemption": "redemptions",
}
ANNOUNCEMENT_SIGNAL_TYPES = {"merger_update", "spinoff"}


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


def _field(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    getter = getattr(item, "get", None)
    if callable(getter):
        value = getter(name)
        if value is not None:
            return value
    return getattr(item, name, None)


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        value = value.value
    return str(value)


def _text_or_json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _date_text(value: Any) -> str | None:
    parsed = _parse_date(value)
    if parsed:
        return parsed.isoformat()
    if isinstance(value, str) and value.strip():
        return value
    return None


def _iso_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _news_window(now: datetime | None = None, *, days_back: int) -> tuple[datetime, datetime]:
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start_date = end.date() - timedelta(days=days_back)
    start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    return start, end


def _compact_payload(item: Any, fields: list[str]) -> dict[str, Any]:
    return {field: _field(item, field) for field in fields if _field(item, field) is not None}


def _provider_payload_state(payload: dict[str, Any]) -> tuple[ProviderState, str] | None:
    if "Error Message" in payload:
        return ProviderState.ERROR, f"Error Message: {payload['Error Message']}"
    if "Information" in payload:
        message = str(payload["Information"])
        state = ProviderState.ERROR if "invalid" in message.lower() else ProviderState.SKIPPED
        return state, f"Information: {message}"
    if "Note" in payload:
        return ProviderState.SKIPPED, f"Note: {payload['Note']}"
    return None


def _alpha_vantage_symbol(value: Any) -> str:
    return str(value or "").upper().split(":")[-1]


def _matches_ma_text(*values: Any) -> bool:
    haystack = " ".join(str(value or "") for value in values).lower()
    return any(keyword in haystack for keyword in MA_KEYWORDS)


def _alpaca_headers(config: Config) -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": config.alpaca_api_key or "",
        "APCA-API-SECRET-KEY": config.alpaca_secret_key or "",
    }


def _corporate_action_items(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    sources: list[Any] = []
    for key in ("corporate_actions", "corporate_actions_v2", "data"):
        value = payload.get(key)
        if value is not None:
            sources.append(value)
    sources.append(payload)

    items: list[tuple[str, dict[str, Any]]] = []
    seen: set[int] = set()
    for source in sources:
        if isinstance(source, dict):
            for event_type, rows in source.items():
                normalized = CORPORATE_ACTION_EVENT_ALIASES.get(event_type, event_type)
                if normalized not in CORPORATE_ACTION_EVENT_FIELDS:
                    continue
                row_list = rows if isinstance(rows, list) else []
                for row in row_list:
                    if isinstance(row, dict) and id(row) not in seen:
                        seen.add(id(row))
                        items.append((normalized, row))
        elif isinstance(source, list):
            for row in source:
                if not isinstance(row, dict) or id(row) in seen:
                    continue
                event_type = str(row.get("type") or row.get("corporate_action_type") or "corporate_action")
                normalized = CORPORATE_ACTION_EVENT_ALIASES.get(event_type, event_type)
                if normalized not in CORPORATE_ACTION_EVENT_FIELDS:
                    continue
                seen.add(id(row))
                items.append((normalized, row))
    return items


def _alpaca_corporate_actions(config: Config, tickers: list[str], *, today: date | None = None) -> tuple[list[EvidenceRecord], ProviderHealth]:
    run_date = today or date.today()
    api_start = run_date - timedelta(days=ALPACA_CORP_API_LOOKBACK_DAYS)
    api_end = run_date + timedelta(days=ALPACA_CORP_API_LOOKAHEAD_DAYS)
    op_start = run_date - timedelta(days=ALPACA_CORP_OPERATIONAL_LOOKBACK_DAYS)
    op_end = run_date + timedelta(days=ALPACA_CORP_OPERATIONAL_LOOKAHEAD_DAYS)
    requested = set(tickers)
    records: list[EvidenceRecord] = []
    for chunk in _chunks(tickers, MAX_SYMBOLS_PER_REQUEST):
        page_token: str | None = None
        pages = 0
        while pages < ALPACA_NEWS_MAX_PAGES:
            payload = _json_get(
                ALPACA_CORPORATE_ACTIONS_URL,
                {
                    "symbols": ",".join(chunk),
                    "region": "us",
                    "start": api_start.isoformat(),
                    "end": api_end.isoformat(),
                    "limit": 1000,
                    "page_token": page_token,
                },
                _alpaca_headers(config),
            )
            pages += 1
            for action_type, item in _corporate_action_items(payload):
                affected_field, counterparty_field, relevant_date_field, role_hint = CORPORATE_ACTION_EVENT_FIELDS[action_type]
                ticker = str(item.get(affected_field) or "").strip().upper()
                if ticker not in requested:
                    continue
                relevant_date = _parse_date(item.get(relevant_date_field))
                if relevant_date and not (op_start <= relevant_date <= op_end):
                    continue
                counterparty = str(item.get(counterparty_field) or "").strip().upper() if counterparty_field else None
                raw_fields = [affected_field]
                if counterparty_field:
                    raw_fields.append(counterparty_field)
                raw_fields.extend([relevant_date_field, "process_date", "record_date", "ex_date"])
                raw = _compact_payload(
                    item,
                    raw_fields,
                )
                records.append(
                    EvidenceRecord(
                        ticker=ticker,
                        provider="alpaca_corporate_actions",
                        source_type="corporate_action",
                        strength=EvidenceStrength.STRUCTURED,
                        source_date=_date_text(item.get(relevant_date_field)) or _date_text(item.get("process_date")),
                        title=f"Alpaca corporate action: {action_type}",
                        summary=json.dumps(raw, sort_keys=True, default=str),
                        role_hint=role_hint,
                        metadata={
                            "action_type": action_type,
                            "counterparty": counterparty,
                            "affected_field": affected_field,
                            "relevant_date_field": relevant_date_field,
                            "raw": raw,
                        },
                    )
                )
            page_token = payload.get("next_page_token")
            if not page_token:
                break
    return records, ProviderHealth(
        provider="alpaca_corporate_actions",
        state=ProviderState.OK,
        records=len(records),
        message=(
            f"{len(records)} records; api_window={api_start.isoformat()}..{api_end.isoformat()}; "
            f"operational_window={op_start.isoformat()}..{op_end.isoformat()}"
        ),
    )


def _alpaca_news(config: Config, tickers: list[str]) -> tuple[list[EvidenceRecord], ProviderHealth]:
    start, end = _news_window(days_back=ALPACA_NEWS_LOOKBACK_DAYS)
    records: list[EvidenceRecord] = []
    for chunk in _chunks(tickers, MAX_SYMBOLS_PER_REQUEST):
        page_token: str | None = None
        pages = 0
        while pages < MAX_PROVIDER_PAGES:
            payload = _json_get(
                ALPACA_NEWS_URL,
                {
                    "symbols": ",".join(chunk),
                    "start": _iso_datetime(start),
                    "end": _iso_datetime(end),
                    "limit": ALPACA_NEWS_PAGE_LIMIT,
                    "include_content": "true",
                    "sort": "desc",
                    "page_token": page_token,
                },
                _alpaca_headers(config),
            )
            pages += 1
            for item in _payload_items(payload, "news"):
                symbols = [str(symbol).upper() for symbol in item.get("symbols") or []]
                title = item.get("headline") or item.get("title")
                summary = _text_or_json(item.get("summary") or item.get("content"))
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
                            title=_text_or_json(title),
                            summary=summary,
                            url=item.get("url"),
                            metadata={"article_id": item.get("id")},
                        )
                    )
            page_token = payload.get("next_page_token")
            if not page_token:
                break
    return records, ProviderHealth(
        provider="alpaca_news",
        state=ProviderState.OK,
        records=len(records),
        message=f"{len(records)} records; window={_iso_datetime(start)}..{_iso_datetime(end)}",
    )


def _fetch_alpaca_announcements(config: Config, since: date, until: date) -> Any:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import CorporateActionDateType, CorporateActionType
    from alpaca.trading.requests import GetCorporateAnnouncementsRequest

    client = TradingClient(api_key=config.alpaca_api_key, secret_key=config.alpaca_secret_key, paper=True)
    if not hasattr(client, "get_corporate_announcements"):
        raise AttributeError("TradingClient.get_corporate_announcements unavailable")

    request = GetCorporateAnnouncementsRequest(
        ca_types=[CorporateActionType.MERGER, CorporateActionType.SPINOFF],
        since=since,
        until=until,
        date_type=CorporateActionDateType.DECLARATION_DATE,
    )
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*deprecated.*")
        return client.get_corporate_announcements(request)


def _alpaca_announcements(config: Config, tickers: list[str], *, today: date | None = None) -> tuple[list[EvidenceRecord], ProviderHealth]:
    run_date = today or date.today()
    days_back = min(ALPACA_ANNOUNCEMENTS_LOOKBACK_DAYS, ALPACA_ANNOUNCEMENTS_MAX_LOOKBACK_DAYS)
    since = run_date - timedelta(days=days_back)
    until = run_date
    requested = set(tickers)
    try:
        fetched = _fetch_alpaca_announcements(config, since, until)
    except ImportError as exc:
        return [], ProviderHealth(
            provider="alpaca_announcements",
            state=ProviderState.SKIPPED,
            message=f"optional alpaca-py dependency unavailable: {exc!r}",
        )
    except AttributeError as exc:
        return [], ProviderHealth(provider="alpaca_announcements", state=ProviderState.SKIPPED, message=str(exc))

    records: list[EvidenceRecord] = []
    seen_ids: set[str] = set()
    if isinstance(fetched, dict):
        items = fetched.get("corporate_announcements") or fetched.get("announcements") or fetched.get("data") or []
    elif isinstance(fetched, list):
        items = fetched
    else:
        try:
            items = list(fetched or [])
        except TypeError:
            items = []
    for item in items:
        ann_id = str(_field(item, "id") or "")
        if ann_id and ann_id in seen_ids:
            continue
        if ann_id:
            seen_ids.add(ann_id)
        sub_type = (_enum_value(_field(item, "ca_sub_type")) or "").lower()
        if sub_type not in ANNOUNCEMENT_SIGNAL_TYPES:
            continue

        initiating = str(_field(item, "initiating_symbol") or "").strip().upper()
        target = str(_field(item, "target_symbol") or "").strip().upper()
        touched: list[tuple[str, str, str | None]] = []
        if initiating in requested:
            touched.append((initiating, "initiating/acquirer", target or None))
        if target in requested:
            touched.append((target, "target/acquiree", initiating or None))
        if not touched:
            continue

        raw = _compact_payload(
            item,
            [
                "id",
                "ca_sub_type",
                "initiating_symbol",
                "target_symbol",
                "declaration_date",
                "ex_date",
                "record_date",
                "payable_date",
            ],
        )
        if "ca_sub_type" in raw:
            raw["ca_sub_type"] = _enum_value(raw["ca_sub_type"])
        for ticker, role_hint, counterparty in touched:
            records.append(
                EvidenceRecord(
                    ticker=ticker,
                    provider="alpaca_announcements",
                    source_type="announcement",
                    strength=EvidenceStrength.STRUCTURED,
                    source_date=_date_text(_field(item, "declaration_date")),
                    title=f"Alpaca announcement: {sub_type}",
                    summary=json.dumps(raw, sort_keys=True, default=str),
                    role_hint=role_hint,
                    metadata={"announcement_id": ann_id, "ca_sub_type": sub_type, "counterparty": counterparty, "raw": raw},
                )
            )
    return records, ProviderHealth(
        provider="alpaca_announcements",
        state=ProviderState.OK,
        records=len(records),
        message=f"{len(records)} records; declaration_window={since.isoformat()}..{until.isoformat()}",
    )


def _alpha_vantage_news(config: Config, tickers: list[str]) -> tuple[list[EvidenceRecord], ProviderHealth]:
    if not tickers:
        return [], ProviderHealth(provider="alpha_vantage", state=ProviderState.SKIPPED, message="no tickers supplied")
    start, end = _news_window(days_back=ALPHA_VANTAGE_NEWS_LOOKBACK_DAYS)
    time_from = start.strftime("%Y%m%dT%H%M")
    time_to = end.strftime("%Y%m%dT%H%M")
    budget = config.alpha_vantage_max_tickers
    if budget <= 0:
        return [], ProviderHealth(provider="alpha_vantage", state=ProviderState.SKIPPED, message="ticker budget is 0")

    requested = sorted(set(tickers))
    to_query = requested[:budget]
    records: list[EvidenceRecord] = []
    queried = 0
    for ticker in to_query:
        if queried:
            time_module.sleep(config.alpha_vantage_request_interval_seconds)
        payload = _json_get(
            ALPHA_VANTAGE_URL,
            {
                "function": "NEWS_SENTIMENT",
                "tickers": ticker,
                "topics": "mergers_and_acquisitions",
                "time_from": time_from,
                "time_to": time_to,
                "sort": "LATEST",
                "limit": ALPHA_VANTAGE_NEWS_LIMIT,
                "apikey": config.alpha_vantage_api_key,
            },
        )
        queried += 1
        provider_message = _provider_payload_state(payload)
        if provider_message:
            state, message = provider_message
            return records, ProviderHealth(
                provider="alpha_vantage",
                state=state,
                records=len(records),
                message=redact_text(
                    f"{message}; queried={queried}/{len(to_query)}; budget={budget}; window={time_from}..{time_to}",
                    config.secret_values(),
                ),
            )
        for item in _payload_items(payload, "feed"):
            title = _text_or_json(item.get("title"))
            summary = _text_or_json(item.get("summary"))
            ticker_sentiment = item.get("ticker_sentiment") or []
            symbols = [
                _alpha_vantage_symbol(entry.get("ticker"))
                for entry in ticker_sentiment
                if isinstance(entry, dict) and entry.get("ticker")
            ]
            if symbols and ticker not in symbols:
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
    state = ProviderState.SKIPPED if len(requested) > budget else ProviderState.OK
    coverage = f"queried={queried}/{len(requested)}; budget={budget}; window={time_from}..{time_to}"
    return records, ProviderHealth(provider="alpha_vantage", state=state, records=len(records), message=f"{len(records)} records; {coverage}")


def _fetch_alpaca_market_bars(config: Config, symbols: list[str], start: date, end: date) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    from alpaca.data.enums import Adjustment
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    try:
        from alpaca.data.enums import DataFeed
    except ImportError:  # pragma: no cover - depends on alpaca-py version
        DataFeed = None

    client = StockHistoricalDataClient(api_key=config.alpaca_api_key, secret_key=config.alpaca_secret_key)
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)
    rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for chunk in _chunks(symbols, MAX_SYMBOLS_PER_REQUEST):
        kwargs: dict[str, Any] = {
            "symbol_or_symbols": chunk,
            "timeframe": TimeFrame.Day,
            "start": start_dt,
            "end": end_dt,
            "adjustment": Adjustment.ALL,
        }
        if config.alpaca_market_data_feed:
            kwargs["feed"] = DataFeed(config.alpaca_market_data_feed) if DataFeed else config.alpaca_market_data_feed
        try:
            response = client.get_stock_bars(StockBarsRequest(**kwargs))
        except Exception as exc:
            errors.append(redact_text(repr(exc), config.secret_values()))
            continue
        for symbol, rows in _alpaca_bars_response_rows(response).items():
            rows_by_symbol.setdefault(symbol, []).extend(rows)
    return rows_by_symbol, errors


def _alpaca_bars_response_rows(response: Any) -> dict[str, list[dict[str, Any]]]:
    if isinstance(response, dict):
        raw_bars = response.get("bars", response)
        if isinstance(raw_bars, dict):
            rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
            for symbol, rows in raw_bars.items():
                if not isinstance(rows, list):
                    continue
                converted_rows = []
                for row in rows:
                    converted = _alpaca_bar_to_row(row)
                    if converted:
                        converted_rows.append(converted)
                if converted_rows:
                    rows_by_symbol[str(symbol).upper()] = converted_rows
            return rows_by_symbol
    frame = getattr(response, "df", None)
    if frame is not None:
        rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
        for _index, row in frame.reset_index().iterrows():
            symbol = _field(row, "symbol")
            if not symbol or str(symbol).lower() == "nan":
                continue
            converted = _alpaca_bar_to_row(row)
            if converted:
                rows_by_symbol.setdefault(str(symbol).upper(), []).append(converted)
        return rows_by_symbol
    return {}


def _alpaca_bar_to_row(row: Any) -> dict[str, Any] | None:
    timestamp = _field(row, "timestamp") or _field(row, "date") or _field(row, "t")
    close = _field(row, "close")
    if close is None:
        close = _field(row, "c")
    if timestamp is None or close is None:
        return None
    return {
        "date": timestamp,
        "open": _field(row, "open") if _field(row, "open") is not None else _field(row, "o"),
        "high": _field(row, "high") if _field(row, "high") is not None else _field(row, "h"),
        "low": _field(row, "low") if _field(row, "low") is not None else _field(row, "l"),
        "close": close,
        "volume": _field(row, "volume") if _field(row, "volume") is not None else _field(row, "v"),
    }


def collect_alpaca_market_evidence(
    config: Config,
    tickers: list[str],
    event_dates_by_ticker: dict[str, list[str]] | None = None,
    *,
    today: date | None = None,
) -> tuple[list[EvidenceRecord], ProviderHealth]:
    requested = sorted({ticker.strip().upper() for ticker in tickers if ticker.strip()})
    if not requested:
        return [], ProviderHealth(provider="alpaca_market_data", state=ProviderState.SKIPPED, message="no selected tickers")
    if not (config.alpaca_api_key and config.alpaca_secret_key):
        return [], ProviderHealth(provider="alpaca_market_data", state=ProviderState.SKIPPED, message="optional provider not configured")
    records: list[EvidenceRecord] = []
    window = market_data_window(today)
    try:
        fetch_result = _fetch_alpaca_market_bars(config, sorted({*requested, "SPY"}), window.start, window.end)
    except ImportError as exc:
        return [], ProviderHealth(
            provider="alpaca_market_data",
            state=ProviderState.SKIPPED,
            message=f"optional alpaca-py dependency unavailable: {exc!r}",
        )
    except Exception as exc:
        return [], ProviderHealth(provider="alpaca_market_data", state=ProviderState.ERROR, message=redact_text(repr(exc), config.secret_values()))
    if isinstance(fetch_result, tuple):
        bars_by_ticker, provider_errors = fetch_result
    else:  # pragma: no cover - compatibility for direct test monkeypatches
        bars_by_ticker = fetch_result
        provider_errors = []
    benchmark_rows = bars_by_ticker.get("SPY")
    returned_symbols: list[str] = []
    for ticker in requested:
        row_list = bars_by_ticker.get(ticker) or []
        if not row_list:
            continue
        context = build_market_context(
            ticker,
            row_list,
            benchmark_rows=benchmark_rows,
            event_dates=(event_dates_by_ticker or {}).get(ticker),
            analysis_date=window.analysis_trading_date,
        )
        if not context:
            continue
        bars, _malformed = normalize_market_rows(row_list)
        returned_symbols.append(ticker)
        latest = context["latest"]
        returns = context["returns"]
        window_return = returns.get("return_5_session")
        if window_return is None:
            window_return = returns.get("return_since_first_available")
        metadata: dict[str, Any] = {
            "first_close": bars[0].close if bars else None,
            "latest_close": latest["close"],
            "latest_volume": latest["volume"],
            "rows": context["coverage"]["rows"],
            "window_return": window_return,
            "market_context": context,
        }
        records.append(
            EvidenceRecord(
                ticker=ticker,
                provider="alpaca_market_data",
                source_type="market_data",
                strength=EvidenceStrength.MARKET,
                source_date=latest["date"],
                summary=(
                    "Alpaca market context: "
                    f"latest_close={latest['close']}, latest_volume={latest['volume']}, rows={metadata['rows']}, "
                    f"return_5_session={returns.get('return_5_session')}, return_20_session={returns.get('return_20_session')}"
                ),
                metadata=metadata,
            )
        )
    missing = sorted(set(requested) - set(returned_symbols))
    benchmark_state = "ok" if benchmark_rows else "missing"
    feed = config.alpaca_market_data_feed or "default"
    message = (
        f"{len(records)} records; window={window.start.isoformat()}..{window.end.isoformat()}; "
        f"requested_symbols={len(requested)}; returned_symbols={len(returned_symbols)}; missing_symbols={len(missing)}; "
        f"benchmark=SPY:{benchmark_state}; feed={feed}"
    )
    if missing:
        message = f"{message}; missing_sample={','.join(missing[:5])}"
    if provider_errors:
        message = f"{message}; provider_errors={len(provider_errors)}; first_error={provider_errors[0]}"
    state = ProviderState.ERROR if provider_errors and not records else ProviderState.OK
    return records, ProviderHealth(provider="alpaca_market_data", state=state, records=len(records), message=message)


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
        for provider, collector in [
            ("alpaca_corporate_actions", _alpaca_corporate_actions),
            ("alpaca_announcements", _alpaca_announcements),
            ("alpaca_news", _alpaca_news),
        ]:
            try:
                provider_records, provider_health = collector(config, requested)
                records.extend(provider_records)
                health.append(provider_health)
            except Exception as exc:
                health.append(ProviderHealth(provider=provider, state=ProviderState.ERROR, message=redact_text(repr(exc), config.secret_values())))
    else:
        message = "no tickers supplied" if alpaca_configured else "optional provider not configured"
        for provider in ("alpaca_corporate_actions", "alpaca_announcements", "alpaca_news"):
            health.append(ProviderHealth(provider=provider, state=ProviderState.SKIPPED, message=message))

    if alpha_configured and requested:
        try:
            alpha_records, alpha_health = _alpha_vantage_news(config, requested)
            records.extend(alpha_records)
            health.append(alpha_health)
        except Exception as exc:
            health.append(ProviderHealth(provider="alpha_vantage", state=ProviderState.ERROR, message=redact_text(repr(exc), config.secret_values())))
    else:
        message = "no tickers supplied" if alpha_configured else "optional provider not configured"
        health.append(ProviderHealth(provider="alpha_vantage", state=ProviderState.SKIPPED, message=message))

    return records, health
