from pathlib import Path

from ma_blacklist_governance.config import Config
from ma_blacklist_governance.workflows import discover


FIXTURES = Path(__file__).parent / "fixtures"


def _config(tmp_path):
    return Config.from_env(
        {"ROBOT_WEALTH_API_KEY": "SYNTHETIC_RW_KEY", "OPENAI_API_KEY": "SYNTHETIC_OPENAI_KEY"},
        output_root=tmp_path,
    )


def test_discovery_scans_full_universe_and_selects_only_news_candidates(tmp_path):
    result = discover(
        _config(tmp_path),
        universe_json=FIXTURES / "rw_universe.json",
        news_json=FIXTURES / "yfinance_news.json",
        market_json=FIXTURES / "yfinance_market.json",
        output_root=tmp_path,
        run_id="run1",
    )

    assert result.denominator_count == 5
    assert [candidate.ticker for candidate in result.candidates] == ["AAA"]
    assert result.candidates[0].input_status == "watchlist_only"
    market_records = [record for record in result.candidates[0].evidence if record.source_type == "market_data"]
    assert market_records
    assert market_records[0].metadata["market_context"]["coverage"]["rows"] == 2
    assert result.openai_called is False


def test_discovery_writes_no_candidates_without_governance(tmp_path):
    result = discover(
        _config(tmp_path),
        universe_json=FIXTURES / "rw_universe.json",
        news_json=FIXTURES / "yfinance_no_candidates.json",
        output_root=tmp_path,
        run_id="run2",
    )

    assert result.selected_count == 0
    assert result.openai_called is False
    report = Path(result.artifacts.report_path).read_text()
    assert "No deterministic discovery candidates were found" in report


def test_rich_structured_evidence_is_scanner_candidate(tmp_path):
    result = discover(
        _config(tmp_path),
        universe_json=FIXTURES / "rw_universe.json",
        news_json=FIXTURES / "yfinance_no_candidates.json",
        rich_evidence_json=FIXTURES / "rich_evidence.json",
        market_json=FIXTURES / "yfinance_market.json",
        output_root=tmp_path,
        run_id="run3",
    )

    assert [candidate.ticker for candidate in result.candidates] == ["DDD"]
    assert result.candidates[0].input_status == "scanner_candidate"


def test_rich_evidence_outside_universe_is_excluded(tmp_path):
    rich = tmp_path / "rich.json"
    rich.write_text(
        '{"evidence":[{"ticker":"ZZZ","provider":"alpaca_announcements","source_type":"announcement","strength":"structured","title":"ZZZ merger"}]}',
        encoding="utf-8",
    )

    result = discover(
        _config(tmp_path),
        universe_json=FIXTURES / "rw_universe.json",
        news_json=FIXTURES / "yfinance_no_candidates.json",
        rich_evidence_json=rich,
        output_root=tmp_path,
        run_id="run4",
    )

    assert result.candidates == []
