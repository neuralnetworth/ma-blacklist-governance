from pathlib import Path

from ma_blacklist_governance.cli import main


FIXTURES = Path(__file__).parent / "fixtures"


def test_help_runs(capsys):
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "promotion-review" in capsys.readouterr().out


def test_preflight_missing_required_exits_nonzero(capsys, monkeypatch):
    monkeypatch.delenv("ROBOT_WEALTH_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    code = main(["preflight"])

    assert code == 2
    assert "missing required credential" in capsys.readouterr().out


def test_discover_cli_smoke(tmp_path):
    code = main(
        [
            "discover",
            "--universe-json",
            str(FIXTURES / "rw_universe.json"),
            "--news-json",
            str(FIXTURES / "yfinance_news.json"),
            "--market-json",
            str(FIXTURES / "yfinance_market.json"),
            "--output-root",
            str(tmp_path),
            "--run-id",
            "cli-discover",
        ]
    )

    assert code == 0
    assert (tmp_path / "cli-discover" / "discover" / "discovery.json").exists()


def test_review_cli_smokes_do_not_mutate_inputs(tmp_path):
    blacklist = tmp_path / "blacklist.txt"
    blacklist.write_text("ZZZ\n")
    before = blacklist.read_text()

    promo_code = main(
        [
            "promotion-review",
            "--offline-fake-governance",
            "--universe-json",
            str(FIXTURES / "rw_universe.json"),
            "--news-json",
            str(FIXTURES / "yfinance_news.json"),
            "--market-json",
            str(FIXTURES / "yfinance_market.json"),
            "--output-root",
            str(tmp_path),
            "--run-id",
            "cli-promo",
        ]
    )
    exit_code = main(
        [
            "durable-exit-review",
            "--offline-fake-governance",
            "--blacklist-file",
            str(blacklist),
            "--universe-json",
            str(FIXTURES / "rw_universe.json"),
            "--news-json",
            str(FIXTURES / "yfinance_no_candidates.json"),
            "--market-json",
            str(FIXTURES / "yfinance_market.json"),
            "--output-root",
            str(tmp_path),
            "--run-id",
            "cli-exit",
        ]
    )

    assert promo_code == 0
    assert exit_code == 0
    assert blacklist.read_text() == before


def test_prune_runs_cli(tmp_path):
    run_dir = tmp_path / "old"
    run_dir.mkdir()
    (run_dir / ".ma_blacklist_governance_run").write_text("created_by=ma-blacklist-governance\n")

    code = main(["prune-runs", "--output-root", str(tmp_path), "--older-than-days", "0"])

    assert code == 0
    assert not run_dir.exists()
