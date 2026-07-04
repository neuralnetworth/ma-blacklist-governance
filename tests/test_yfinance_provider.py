import sys
from datetime import date
from types import SimpleNamespace

from ma_blacklist_governance.providers.yfinance_provider import collect_market_evidence


class _FakeHistory:
    def __init__(self, rows):
        self._rows = rows

    def iterrows(self):
        for row in self._rows:
            yield date.fromisoformat(row["date"]), row


def test_market_evidence_keeps_selected_ticker_when_spy_fails(monkeypatch):
    rows = [
        {
            "date": "2026-01-15",
            "Open": 10.0,
            "High": 10.8,
            "Low": 9.9,
            "Close": 10.5,
            "Volume": 1200,
        }
    ]

    class FakeTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        def history(self, **kwargs):
            if self.ticker == "SPY":
                raise RuntimeError("synthetic benchmark outage")
            return _FakeHistory(rows)

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=FakeTicker))

    records, health = collect_market_evidence(["AAA"])

    assert [record.ticker for record in records] == ["AAA"]
    assert records[0].metadata["market_context"]["coverage"]["benchmark_rows"] == 0
    assert health.state == "ok"
    assert "benchmark=SPY:error" in str(health.message)
