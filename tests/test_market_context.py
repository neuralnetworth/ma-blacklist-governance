from datetime import date, timedelta

import pandas_market_calendars as mcal

from ma_blacklist_governance.market_context import build_market_context


def _rows(start: str, sessions: int, *, close_start: float = 100.0, volume_start: int = 1000):
    calendar = mcal.get_calendar("NYSE")
    days = calendar.valid_days(start_date=start, end_date="2026-06-30")[:sessions]
    rows = []
    for index, session in enumerate(days):
        close = close_start + index
        rows.append(
            {
                "date": session.date().isoformat(),
                "open": close - 0.25,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": volume_start + (index * 10),
            }
        )
    return rows


def test_market_context_computes_nyse_aligned_lite_metrics():
    rows = _rows("2026-01-02", 65)
    spy_rows = _rows("2026-01-02", 65, close_start=400.0, volume_start=10_000)
    saturday_anchor = date(2026, 1, 17)

    context = build_market_context(
        "AAA",
        rows,
        benchmark_rows=spy_rows,
        event_dates=[saturday_anchor.isoformat()],
        analysis_date=date(2026, 4, 8),
    )

    assert context is not None
    assert context["coverage"]["rows"] == 65
    assert context["latest"]["close"] == 164.0
    assert context["returns"]["return_5_session"] is not None
    assert context["returns"]["return_20_session"] is not None
    assert context["baseline_60_session"]["trading_days"] == 60
    assert context["trailing_20_session"]["avg_volume"] is not None
    event = context["event_reactions"][0]
    assert event["anchor_date"] == "2026-01-17"
    assert event["reaction_date"] == "2026-01-20"
    assert event["prior_trading_date"] == "2026-01-16"
    assert event["market_adjusted_return_1_session"] is not None


def test_market_context_flags_incomplete_event_windows_and_missing_benchmark():
    rows = _rows("2026-01-02", 5)
    latest_anchor = date.fromisoformat(rows[-1]["date"])

    context = build_market_context(
        "AAA",
        rows,
        event_dates=[latest_anchor.isoformat()],
        analysis_date=latest_anchor + timedelta(days=1),
    )

    assert context is not None
    assert context["coverage"]["rows"] == 5
    assert context["returns"]["return_5_session"] is None
    event = context["event_reactions"][0]
    assert event["reaction_5_session_window_complete"] is False
    assert event["spy_return_1_session"] is None
    assert event["market_adjusted_return_1_session"] is None


def test_market_context_skips_malformed_rows():
    context = build_market_context(
        "AAA",
        [
            {"date": "2026-01-02", "close": "bad", "volume": 100},
            {"date": "2026-01-03", "close": float("nan"), "volume": 150},
            {"date": "2026-01-05", "close": 101.0, "volume": 200},
        ],
        analysis_date=date(2026, 1, 6),
    )

    assert context is not None
    assert context["coverage"]["rows"] == 1
    assert context["coverage"]["malformed_rows"] == 2
    assert context["latest"]["close"] == 101.0
