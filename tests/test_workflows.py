import os
from pathlib import Path

from ma_blacklist_governance.config import Config
from ma_blacklist_governance.providers.openai_governance import OfflineGovernanceClient
from ma_blacklist_governance.workflows import RUN_MARKER, durable_exit_review, promotion_review, prune_runs


FIXTURES = Path(__file__).parent / "fixtures"


def _config(tmp_path):
    return Config.from_env(
        {
            "ROBOT_WEALTH_API_KEY": "SYNTHETIC_RW_KEY",
            "OPENAI_API_KEY": "SYNTHETIC_OPENAI_KEY",
            "ALPACA_API_KEY": "SYNTHETIC_ALPACA_KEY",
        },
        output_root=tmp_path,
    )


def test_promotion_review_governs_only_discovery_set(tmp_path):
    result = promotion_review(
        _config(tmp_path),
        governance_client=OfflineGovernanceClient(),
        universe_json=FIXTURES / "rw_universe.json",
        news_json=FIXTURES / "yfinance_news.json",
        market_json=FIXTURES / "yfinance_market.json",
        output_root=tmp_path,
        run_id="promo1",
    )

    assert result.denominator_count == 5
    assert [item.ticker for item in result.governance_results] == ["AAA"]
    assert result.openai_called is True
    counts = {item.provider: item.count for item in result.governance_results[0].provider_evidence_counts}
    assert counts["yfinance_news"] == 1
    assert counts["yfinance_market"] == 1
    assert Path(result.artifacts.governance_results_path).exists()


def test_promotion_review_no_candidates_does_not_write_governance(tmp_path):
    result = promotion_review(
        _config(tmp_path),
        governance_client=OfflineGovernanceClient(),
        universe_json=FIXTURES / "rw_universe.json",
        news_json=FIXTURES / "yfinance_no_candidates.json",
        output_root=tmp_path,
        run_id="promo2",
    )

    assert result.openai_called is False
    assert result.artifacts.governance_results_path is None
    assert "OpenAI governance called: no" in Path(result.artifacts.report_path).read_text()


def test_promotion_review_with_durable_tickers_excludes_existing_blocks(tmp_path):
    result = promotion_review(
        _config(tmp_path),
        governance_client=OfflineGovernanceClient(),
        durable_tickers=["AAA"],
        universe_json=FIXTURES / "rw_universe.json",
        news_json=FIXTURES / "yfinance_news.json",
        market_json=FIXTURES / "yfinance_market.json",
        output_root=tmp_path,
        run_id="promo-with-durable",
    )

    assert result.selected_count == 0
    assert result.openai_called is False
    assert result.artifacts.governance_results_path is None


def test_promotion_review_with_durable_tickers_keeps_new_operator_candidate(tmp_path):
    result = promotion_review(
        _config(tmp_path),
        governance_client=OfflineGovernanceClient(),
        operator_candidates=["AAA", "FFF"],
        durable_tickers=["AAA"],
        universe_json=FIXTURES / "rw_universe.json",
        news_json=FIXTURES / "yfinance_no_candidates.json",
        market_json=FIXTURES / "yfinance_market.json",
        output_root=tmp_path,
        run_id="promo-with-durable-operator",
    )

    assert [candidate.ticker for candidate in result.candidates] == ["FFF"]
    assert [item.ticker for item in result.governance_results] == ["FFF"]
    assert result.governance_results[0].recommendation == "Do Not Promote"


def test_durable_exit_keeps_out_of_universe_ticker_and_does_not_mutate_input(tmp_path):
    blacklist = tmp_path / "blacklist.txt"
    blacklist.write_text("# synthetic durable list\nZZZ\nAAA\n", encoding="utf-8")
    before = blacklist.read_text()

    result = durable_exit_review(
        _config(tmp_path),
        blacklist_file=blacklist,
        governance_client=OfflineGovernanceClient(),
        universe_json=FIXTURES / "rw_universe.json",
        news_json=FIXTURES / "yfinance_no_candidates.json",
        market_json=FIXTURES / "yfinance_market.json",
        output_root=tmp_path,
        run_id="exit1",
    )

    assert blacklist.read_text() == before
    by_ticker = {candidate.ticker: candidate for candidate in result.candidates}
    assert by_ticker["ZZZ"].pair_context.status == "out_of_universe"
    assert by_ticker["AAA"].pair_context.current_pair_count == 2


def test_artifacts_redact_fake_secrets_and_account_ids(tmp_path):
    result = promotion_review(
        _config(tmp_path),
        governance_client=OfflineGovernanceClient(),
        operator_candidates=["FFF"],
        universe_json=FIXTURES / "rw_universe.json",
        news_json=FIXTURES / "yfinance_no_candidates.json",
        market_json=FIXTURES / "yfinance_market.json",
        output_root=tmp_path,
        run_id="promo3",
    )

    for artifact in [result.artifacts.discovery_path, result.artifacts.provider_health_path, result.artifacts.governance_results_path, result.artifacts.report_path]:
        if artifact:
            text = Path(artifact).read_text()
            assert "SYNTHETIC_RW_KEY" not in text
            assert "SYNTHETIC_OPENAI_KEY" not in text
            assert "SYNTHETIC_ALPACA_KEY" not in text


def test_prune_runs_removes_only_output_dirs(tmp_path):
    output_root = tmp_path / "runs"
    old_run = output_root / "old"
    old_run.mkdir(parents=True)
    (old_run / RUN_MARKER).write_text("created_by=ma-blacklist-governance\n")
    (old_run / "report.md").write_text("old")
    unrelated = output_root / "not-a-run"
    unrelated.mkdir()
    input_file = tmp_path / "blacklist.txt"
    input_file.write_text("AAA\n")

    old = 946684800
    os.utime(old_run, (old, old))
    removed = prune_runs(output_root, older_than_days=0)

    assert old_run in removed
    assert not old_run.exists()
    assert unrelated.exists()
    assert input_file.read_text() == "AAA\n"


def test_invalid_run_id_is_rejected(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        promotion_review(
            _config(tmp_path),
            governance_client=OfflineGovernanceClient(),
            operator_candidates=["AAA"],
            universe_json=FIXTURES / "rw_universe.json",
            news_json=FIXTURES / "yfinance_no_candidates.json",
            output_root=tmp_path,
            run_id="../bad",
        )


def test_governance_failure_writes_partial_artifacts(tmp_path):
    class FailingClient(OfflineGovernanceClient):
        def review(self, candidate, provider_health, run_date):
            if candidate.ticker == "FFF":
                raise RuntimeError("synthetic failure")
            return super().review(candidate, provider_health, run_date)

    result = promotion_review(
        _config(tmp_path),
        governance_client=FailingClient(),
        operator_candidates=["AAA", "FFF"],
        universe_json=FIXTURES / "rw_universe.json",
        news_json=FIXTURES / "yfinance_no_candidates.json",
        market_json=FIXTURES / "yfinance_market.json",
        output_root=tmp_path,
        run_id="promo-partial",
    )

    assert [item.ticker for item in result.governance_results] == ["AAA"]
    assert any(item.provider == "openai:FFF" and item.state == "error" for item in result.provider_health)
    assert Path(result.artifacts.report_path).exists()
