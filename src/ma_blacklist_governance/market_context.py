"""NYSE-aware market context metrics for governance evidence."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable

import pandas_market_calendars as mcal


DEFAULT_MARKET_WINDOW_DAYS = 120


@dataclass(frozen=True)
class MarketWindow:
    start: date
    end: date
    analysis_trading_date: date


@dataclass(frozen=True)
class MarketBar:
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None


def market_data_window(today: date | None = None, *, calendar_days: int = DEFAULT_MARKET_WINDOW_DAYS) -> MarketWindow:
    analysis_date = today or date.today()
    analysis_trading_date = previous_trading_session(analysis_date)
    return MarketWindow(
        start=analysis_trading_date - timedelta(days=calendar_days),
        end=analysis_trading_date,
        analysis_trading_date=analysis_trading_date,
    )


def previous_trading_session(value: date) -> date:
    sessions = _valid_sessions(value - timedelta(days=14), value)
    return sessions[-1] if sessions else value


def next_trading_session(value: date) -> date:
    sessions = _valid_sessions(value, value + timedelta(days=14))
    return sessions[0] if sessions else value


def build_market_context(
    ticker: str,
    rows: Any,
    *,
    benchmark_rows: Any | None = None,
    event_dates: Iterable[Any] | None = None,
    analysis_date: date | None = None,
) -> dict[str, Any] | None:
    bars, malformed = normalize_market_rows(rows)
    if not bars:
        return None

    benchmark_bars, benchmark_malformed = normalize_market_rows(benchmark_rows or [])
    latest = bars[-1]
    analysis_trading_date = previous_trading_session(analysis_date or latest.date)
    expected_sessions = _valid_sessions(bars[0].date, latest.date)
    row_dates = {bar.date for bar in bars}
    benchmark_by_date = {bar.date: bar for bar in benchmark_bars}

    context: dict[str, Any] = {
        "ticker": ticker.upper(),
        "analysis_trading_date": analysis_trading_date.isoformat(),
        "coverage": {
            "rows": len(bars),
            "malformed_rows": malformed,
            "start_date": bars[0].date.isoformat(),
            "end_date": latest.date.isoformat(),
            "expected_trading_sessions": len(expected_sessions),
            "missing_trading_sessions": len([session for session in expected_sessions if session not in row_dates]),
            "benchmark_rows": len(benchmark_bars),
            "benchmark_malformed_rows": benchmark_malformed,
        },
        "latest": {
            "date": latest.date.isoformat(),
            "close": latest.close,
            "volume": latest.volume,
        },
        "returns": {
            "return_5_session": _return_over_available_sessions(bars, 5),
            "return_20_session": _return_over_available_sessions(bars, 20),
            "return_since_first_available": _safe_return(bars[0].close, latest.close),
        },
        "trailing_20_session": _trailing_summary(bars, 20),
        "baseline_60_session": _trailing_summary(bars, 60) if len(bars) >= 60 else None,
        "event_reactions": [],
    }

    for raw_event_date in event_dates or []:
        parsed = _parse_date(raw_event_date)
        if parsed is None:
            continue
        reaction = _event_reaction(bars, benchmark_by_date, parsed)
        if reaction:
            context["event_reactions"].append(reaction)

    return context


def normalize_market_rows(rows: Any) -> tuple[list[MarketBar], int]:
    if rows is None:
        return [], 0
    if isinstance(rows, dict):
        iterable = [rows]
    elif isinstance(rows, list):
        iterable = rows
    else:
        try:
            iterable = list(rows)
        except TypeError:
            return [], 1

    malformed = 0
    bars_by_date: dict[date, MarketBar] = {}
    for item in iterable:
        if not isinstance(item, dict):
            malformed += 1
            continue
        parsed_date = _parse_date(_first_value(item, "date", "Date", "timestamp", "t"))
        close = _float_or_none(_first_value(item, "close", "Close", "c"))
        if parsed_date is None or close is None:
            malformed += 1
            continue
        bars_by_date[parsed_date] = MarketBar(
            date=parsed_date,
            open=_float_or_none(_first_value(item, "open", "Open", "o")),
            high=_float_or_none(_first_value(item, "high", "High", "h")),
            low=_float_or_none(_first_value(item, "low", "Low", "l")),
            close=close,
            volume=_float_or_none(_first_value(item, "volume", "Volume", "v")),
        )
    return sorted(bars_by_date.values(), key=lambda bar: bar.date), malformed


def _event_reaction(bars: list[MarketBar], benchmark_by_date: dict[date, MarketBar], anchor_date: date) -> dict[str, Any] | None:
    reaction_index = next((index for index, bar in enumerate(bars) if bar.date >= anchor_date and bar.date >= next_trading_session(anchor_date)), None)
    if reaction_index is None:
        return None
    reaction_bar = bars[reaction_index]
    prior_bar = bars[reaction_index - 1] if reaction_index > 0 else None
    post_bars = bars[reaction_index + 1 : reaction_index + 6]
    window_complete = len(post_bars) >= 5
    baseline = _trailing_summary(bars[:reaction_index], 60) if reaction_index >= 60 else None
    volume_reaction = None
    if baseline and baseline.get("avg_volume"):
        reaction_window = [reaction_bar, *post_bars[:4]]
        volumes = [bar.volume for bar in reaction_window if bar.volume is not None]
        if volumes:
            volume_reaction = (sum(volumes) / len(volumes)) / baseline["avg_volume"]

    return_1 = _safe_return(prior_bar.close, reaction_bar.close) if prior_bar else None
    return_5 = _safe_return(prior_bar.close, post_bars[4].close) if prior_bar and window_complete else None
    spy_return_1 = _benchmark_return(benchmark_by_date, prior_bar.date if prior_bar else None, reaction_bar.date)
    spy_return_5 = _benchmark_return(benchmark_by_date, prior_bar.date if prior_bar else None, post_bars[4].date if window_complete else None)
    return {
        "anchor_date": anchor_date.isoformat(),
        "reaction_date": reaction_bar.date.isoformat(),
        "prior_trading_date": prior_bar.date.isoformat() if prior_bar else None,
        "gap": _safe_return(prior_bar.close, reaction_bar.open) if prior_bar and reaction_bar.open is not None else None,
        "return_1_session": return_1,
        "return_5_session": return_5,
        "reaction_5_session_window_complete": window_complete,
        "volume_on_reaction_date": reaction_bar.volume,
        "volume_reaction_vs_baseline": volume_reaction,
        "spy_return_1_session": spy_return_1,
        "spy_return_5_session": spy_return_5,
        "market_adjusted_return_1_session": return_1 - spy_return_1 if return_1 is not None and spy_return_1 is not None else None,
        "market_adjusted_return_5_session": return_5 - spy_return_5 if return_5 is not None and spy_return_5 is not None else None,
    }


def _benchmark_return(benchmark_by_date: dict[date, MarketBar], start: date | None, end: date | None) -> float | None:
    if start is None or end is None:
        return None
    start_bar = benchmark_by_date.get(start)
    end_bar = benchmark_by_date.get(end)
    if not start_bar or not end_bar:
        return None
    return _safe_return(start_bar.close, end_bar.close)


def _return_over_available_sessions(bars: list[MarketBar], sessions: int) -> float | None:
    if len(bars) <= sessions:
        return None
    return _safe_return(bars[-sessions - 1].close, bars[-1].close)


def _trailing_summary(bars: list[MarketBar], sessions: int) -> dict[str, Any] | None:
    if not bars:
        return None
    window = bars[-sessions:]
    volumes = [bar.volume for bar in window if bar.volume is not None]
    ranges = [
        (bar.high - bar.low) / bar.close
        for bar in window
        if bar.high is not None and bar.low is not None and bar.close != 0
    ]
    return {
        "start_date": window[0].date.isoformat(),
        "end_date": window[-1].date.isoformat(),
        "trading_days": len(window),
        "avg_volume": (sum(volumes) / len(volumes)) if volumes else None,
        "avg_daily_range": (sum(ranges) / len(ranges)) if ranges else None,
    }


def _valid_sessions(start: date, end: date) -> list[date]:
    if end < start:
        return []
    calendar = mcal.get_calendar("NYSE")
    return [session.date() for session in calendar.valid_days(start_date=start.isoformat(), end_date=end.isoformat())]


def _safe_return(start: float | None, end: float | None) -> float | None:
    if start in (None, 0) or end is None:
        return None
    return (end / start) - 1.0


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return None
    return None


def _first_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed
