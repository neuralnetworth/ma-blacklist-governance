import sys
import types
from datetime import date
from types import SimpleNamespace

from ma_blacklist_governance.config import Config
from ma_blacklist_governance.providers import rich


def _config(overrides=None):
    env = {
        "ROBOT_WEALTH_API_KEY": "SYNTHETIC_RW_KEY",
        "OPENAI_API_KEY": "SYNTHETIC_OPENAI_KEY",
        "ALPACA_API_KEY": "SYNTHETIC_ALPACA_KEY",
        "ALPACA_SECRET_KEY": "SYNTHETIC_ALPACA_SECRET",
        "ALPHA_VANTAGE_API_KEY": "SYNTHETIC_AV_KEY",
        "ALPHA_VANTAGE_REQUEST_INTERVAL_SECONDS": "0",
    }
    env.update(overrides or {})
    return Config.from_env(env)


def test_alpaca_corporate_actions_parse_nested_event_types(monkeypatch):
    calls = []

    def fake_json_get(url, params, headers=None, timeout=30.0):
        calls.append((url, params))
        return {
            "corporate_actions": {
                "cash_mergers": [
                    {
                        "acquiree_symbol": "AAA",
                        "acquirer_symbol": "BBB",
                        "effective_date": "2026-07-20",
                        "process_date": "2026-07-01",
                    }
                ],
                "spin_offs": [{"source_symbol": "CCC", "ex_date": "2026-07-08"}],
                "name_changes": [{"old_symbol": "DDD", "new_symbol": "DDE", "process_date": "2026-07-02"}],
            }
        }

    monkeypatch.setattr(rich, "_json_get", fake_json_get)

    records, health = rich._alpaca_corporate_actions(_config(), ["AAA", "BBB", "CCC", "DDD"], today=date(2026, 7, 3))

    assert health.state == "ok"
    assert "api_window=2026-05-04..2026-09-01" in health.message
    assert "operational_window=2026-06-26..2026-08-02" in health.message
    assert [record.ticker for record in records] == ["AAA", "CCC", "DDD"]
    assert {record.metadata["action_type"] for record in records} == {"cash_mergers", "spin_offs", "name_changes"}
    assert records[0].role_hint == "target/acquiree"
    assert records[0].metadata["counterparty"] == "BBB"
    assert calls[0][1]["start"] == "2026-05-04"
    assert calls[0][1]["end"] == "2026-09-01"


def test_alpaca_news_uses_window_and_page_cap_per_chunk(monkeypatch):
    calls = []

    def fake_json_get(url, params, headers=None, timeout=30.0):
        calls.append(params.copy())
        next_by_token = {None: "page-2", "page-2": "page-3", "page-3": None}
        return {"news": [], "next_page_token": next_by_token[params.get("page_token")]}

    monkeypatch.setattr(rich, "_json_get", fake_json_get)

    tickers = [f"T{index:03d}" for index in range(51)]
    records, health = rich._alpaca_news(_config(), tickers)

    assert records == []
    assert health.state == "ok"
    assert len(calls) == 6
    assert {call["limit"] for call in calls} == {50}
    assert all(call["start"] and call["end"] for call in calls)
    assert len({call["symbols"] for call in calls}) == 2


def test_alpaca_announcements_emit_role_specific_records(monkeypatch):
    fetched = [
        SimpleNamespace(
            id="ann-1",
            ca_sub_type=SimpleNamespace(value="merger_update"),
            initiating_symbol="AAA",
            target_symbol="BBB",
            declaration_date=date(2026, 7, 1),
            ex_date=None,
            record_date=None,
            payable_date=None,
        ),
        SimpleNamespace(
            id="ann-1",
            ca_sub_type=SimpleNamespace(value="merger_update"),
            initiating_symbol="AAA",
            target_symbol="BBB",
            declaration_date=date(2026, 7, 1),
            ex_date=None,
            record_date=None,
            payable_date=None,
        ),
        SimpleNamespace(
            id="ann-2",
            ca_sub_type=SimpleNamespace(value="merger_completion"),
            initiating_symbol="CCC",
            target_symbol="DDD",
            declaration_date=date(2026, 7, 1),
            ex_date=None,
            record_date=None,
            payable_date=None,
        ),
    ]
    windows = []

    def fake_fetch(config, since, until):
        windows.append((since, until))
        return fetched

    monkeypatch.setattr(rich, "_fetch_alpaca_announcements", fake_fetch)

    records, health = rich._alpaca_announcements(_config(), ["AAA", "BBB", "CCC"], today=date(2026, 7, 3))

    assert health.state == "ok"
    assert "declaration_window=2026-06-03..2026-07-03" in health.message
    assert windows == [(date(2026, 6, 3), date(2026, 7, 3))]
    assert [(record.ticker, record.role_hint) for record in records] == [
        ("AAA", "initiating/acquirer"),
        ("BBB", "target/acquiree"),
    ]
    assert all(record.provider == "alpaca_announcements" for record in records)


def test_alpha_vantage_queries_per_ticker_and_marks_information_skipped(monkeypatch):
    calls = []

    def fake_json_get(url, params, headers=None, timeout=30.0):
        calls.append(params.copy())
        if params["tickers"] == "AAA":
            return {
                "feed": [
                    {
                        "title": "AAA merger update",
                        "summary": "AAA announces acquisition update.",
                        "time_published": "20260702T120000",
                        "url": "https://example.test/aaa",
                        "ticker_sentiment": [{"ticker": "AAA"}],
                        "topics": [{"topic": "Mergers & Acquisitions"}],
                        "overall_sentiment_label": "Neutral",
                    }
                ]
            }
        return {"Information": "synthetic rate limit"}

    monkeypatch.setattr(rich, "_json_get", fake_json_get)

    records, health = rich._alpha_vantage_news(_config(), ["AAA", "BBB", "CCC"])

    assert [record.ticker for record in records] == ["AAA"]
    assert health.state == "skipped"
    assert health.records == 1
    assert "Information: synthetic rate limit" in health.message
    assert [call["tickers"] for call in calls] == ["AAA", "BBB"]
    assert all("," not in call["tickers"] for call in calls)
    assert all(call["time_from"] and call["time_to"] and call["limit"] == 100 for call in calls)


def test_alpha_vantage_error_message_is_not_ok_zero(monkeypatch):
    def fake_json_get(url, params, headers=None, timeout=30.0):
        return {"Error Message": "synthetic invalid input"}

    monkeypatch.setattr(rich, "_json_get", fake_json_get)

    records, health = rich._alpha_vantage_news(_config(), ["AAA"])

    assert records == []
    assert health.state == "error"
    assert health.records == 0
    assert "Error Message: synthetic invalid input" in health.message


def test_alpha_vantage_invalid_information_is_error(monkeypatch):
    def fake_json_get(url, params, headers=None, timeout=30.0):
        return {"Information": "Invalid inputs. Please refer to the API documentation."}

    monkeypatch.setattr(rich, "_json_get", fake_json_get)

    records, health = rich._alpha_vantage_news(_config(), ["AAA"])

    assert records == []
    assert health.state == "error"
    assert "Information: Invalid inputs" in health.message


def _market_rows(start: str = "2026-01-02", sessions: int = 65, close_start: float = 100.0):
    import pandas_market_calendars as mcal

    calendar = mcal.get_calendar("NYSE")
    days = calendar.valid_days(start_date=start, end_date="2026-06-30")[:sessions]
    return [
        {
            "date": session.date().isoformat(),
            "open": close_start + index - 0.2,
            "high": close_start + index + 1.0,
            "low": close_start + index - 1.0,
            "close": close_start + index,
            "volume": 1000 + index,
        }
        for index, session in enumerate(days)
    ]


def test_alpaca_market_data_uses_window_and_reports_partial_coverage(monkeypatch):
    calls = []

    def fake_fetch(config, symbols, start, end):
        calls.append((symbols, start, end))
        return {"AAA": _market_rows(), "SPY": _market_rows(close_start=400.0)}

    monkeypatch.setattr(rich, "_fetch_alpaca_market_bars", fake_fetch)

    records, health = rich.collect_alpaca_market_evidence(
        _config(),
        ["AAA", "BBB"],
        event_dates_by_ticker={"AAA": ["2026-01-17"]},
        today=date(2026, 4, 8),
    )

    assert [record.ticker for record in records] == ["AAA"]
    assert records[0].metadata["market_context"]["event_reactions"][0]["reaction_date"] == "2026-01-20"
    assert health.state == "ok"
    assert health.records == 1
    assert "requested_symbols=2" in health.message
    assert "returned_symbols=1" in health.message
    assert "missing_symbols=1" in health.message
    assert "benchmark=SPY:ok" in health.message
    assert "window=" in health.message
    assert calls[0][0] == ["AAA", "BBB", "SPY"]


def test_alpaca_market_data_zero_records_still_reports_diagnostics(monkeypatch):
    def fake_fetch(config, symbols, start, end):
        return {"SPY": _market_rows(close_start=400.0)}

    monkeypatch.setattr(rich, "_fetch_alpaca_market_bars", fake_fetch)

    records, health = rich.collect_alpaca_market_evidence(_config(), ["AAA"], today=date(2026, 4, 8))

    assert records == []
    assert health.state == "ok"
    assert health.records == 0
    assert "requested_symbols=1" in health.message
    assert "returned_symbols=0" in health.message
    assert "missing_symbols=1" in health.message


def test_alpaca_market_data_keeps_partial_rows_when_provider_chunk_errors(monkeypatch):
    def fake_fetch(config, symbols, start, end):
        return {"AAA": _market_rows(), "SPY": _market_rows(close_start=400.0)}, ["RuntimeError('synthetic chunk failure')"]

    monkeypatch.setattr(rich, "_fetch_alpaca_market_bars", fake_fetch)

    records, health = rich.collect_alpaca_market_evidence(_config(), ["AAA", "BBB"], today=date(2026, 4, 8))

    assert [record.ticker for record in records] == ["AAA"]
    assert health.state == "ok"
    assert "provider_errors=1" in str(health.message)
    assert "missing_symbols=1" in str(health.message)


def test_alpaca_market_fetch_retains_successful_chunks(monkeypatch):
    calls = []

    class Adjustment:
        ALL = "all"

    class DataFeed:
        def __init__(self, value):
            self.value = value

    class StockBarsRequest:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class StockHistoricalDataClient:
        def __init__(self, api_key, secret_key):
            assert api_key == "SYNTHETIC_ALPACA_KEY"
            assert secret_key == "SYNTHETIC_ALPACA_SECRET"

        def get_stock_bars(self, request):
            calls.append(request.kwargs)
            if len(calls) == 2:
                raise RuntimeError("synthetic second chunk failure")
            return {"bars": {"AAA": _market_rows(sessions=2)}}

    class TimeFrame:
        Day = "day"

    monkeypatch.setitem(sys.modules, "alpaca", types.ModuleType("alpaca"))
    monkeypatch.setitem(sys.modules, "alpaca.data", types.ModuleType("alpaca.data"))
    monkeypatch.setitem(
        sys.modules,
        "alpaca.data.enums",
        types.SimpleNamespace(Adjustment=Adjustment, DataFeed=DataFeed),
    )
    monkeypatch.setitem(
        sys.modules,
        "alpaca.data.historical",
        types.SimpleNamespace(StockHistoricalDataClient=StockHistoricalDataClient),
    )
    monkeypatch.setitem(sys.modules, "alpaca.data.requests", types.SimpleNamespace(StockBarsRequest=StockBarsRequest))
    monkeypatch.setitem(sys.modules, "alpaca.data.timeframe", types.SimpleNamespace(TimeFrame=TimeFrame))
    monkeypatch.setattr(rich, "MAX_SYMBOLS_PER_REQUEST", 1)

    config = _config({"ALPACA_MARKET_DATA_FEED": "iex"})
    rows_by_symbol, errors = rich._fetch_alpaca_market_bars(config, ["AAA", "BBB"], date(2026, 1, 1), date(2026, 1, 31))

    assert list(rows_by_symbol) == ["AAA"]
    assert len(rows_by_symbol["AAA"]) == 2
    assert len(errors) == 1
    assert calls[0]["symbol_or_symbols"] == ["AAA"]
    assert calls[0]["adjustment"] == "all"
    assert calls[0]["timeframe"] == "day"
    assert calls[0]["feed"].value == "iex"


def test_alpaca_market_data_preserves_zero_five_session_return(monkeypatch):
    rows = _market_rows()
    flat_close = rows[-6]["close"]
    for row in rows[-5:]:
        row["open"] = flat_close
        row["high"] = flat_close
        row["low"] = flat_close
        row["close"] = flat_close

    def fake_fetch(config, symbols, start, end):
        return {"AAA": rows}

    monkeypatch.setattr(rich, "_fetch_alpaca_market_bars", fake_fetch)

    records, health = rich.collect_alpaca_market_evidence(_config(), ["AAA"], today=date(2026, 4, 8))

    assert health.state == "ok"
    assert records[0].metadata["market_context"]["returns"]["return_5_session"] == 0.0
    assert records[0].metadata["window_return"] == 0.0


def test_alpaca_market_data_import_failure_is_skipped(monkeypatch):
    def fake_fetch(config, symbols, start, end):
        raise ImportError("synthetic missing alpaca")

    monkeypatch.setattr(rich, "_fetch_alpaca_market_bars", fake_fetch)

    records, health = rich.collect_alpaca_market_evidence(_config(), ["AAA"], today=date(2026, 4, 8))

    assert records == []
    assert health.state == "skipped"
    assert "optional alpaca-py dependency unavailable" in health.message
