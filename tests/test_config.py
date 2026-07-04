from ma_blacklist_governance.config import Config


def test_preflight_redacts_required_credentials():
    config = Config.from_env(
        {
            "ROBOT_WEALTH_API_KEY": "SYNTHETIC_RW_KEY",
            "OPENAI_API_KEY": "SYNTHETIC_OPENAI_KEY",
        }
    )
    ok, health = config.preflight()

    assert ok is True
    assert {item.provider for item in health} >= {"robot_wealth", "openai", "yfinance", "alpaca", "alpha_vantage"}


def test_preflight_missing_required_fails():
    config = Config.from_env({})
    ok, health = config.preflight()

    assert ok is False
    errors = {item.provider for item in health if item.state == "error"}
    assert {"robot_wealth", "openai"} <= errors
    skipped = {item.provider for item in health if item.state == "skipped"}
    assert {"alpaca", "alpha_vantage"} <= skipped


def test_explicit_output_root_overrides_environment(tmp_path):
    config = Config.from_env({"MA_BLACKLIST_OUTPUT_ROOT": "from-env"}, output_root=tmp_path / "from-cli")

    assert config.output_root == tmp_path / "from-cli"


def test_openai_reasoning_effort_defaults_to_medium():
    config = Config.from_env({})

    assert config.openai_reasoning_effort == "medium"


def test_openai_reasoning_effort_accepts_supported_values():
    config = Config.from_env({"OPENAI_REASONING_EFFORT": "minimal"})

    assert config.openai_reasoning_effort == "minimal"


def test_openai_reasoning_effort_rejects_unsupported_values():
    try:
        Config.from_env({"OPENAI_REASONING_EFFORT": "ultra"})
    except ValueError as exc:
        assert "OPENAI_REASONING_EFFORT must be one of" in str(exc)
    else:
        raise AssertionError("unsupported OPENAI_REASONING_EFFORT did not raise")


def test_alpha_vantage_ticker_budget_is_configurable():
    config = Config.from_env({"ALPHA_VANTAGE_MAX_TICKERS": "7"})

    assert config.alpha_vantage_max_tickers == 7


def test_alpha_vantage_ticker_budget_rejects_negative_values():
    try:
        Config.from_env({"ALPHA_VANTAGE_MAX_TICKERS": "-1"})
    except ValueError as exc:
        assert "ALPHA_VANTAGE_MAX_TICKERS must be greater than or equal to 0" in str(exc)
    else:
        raise AssertionError("negative ALPHA_VANTAGE_MAX_TICKERS did not raise")


def test_alpha_vantage_request_interval_is_configurable():
    config = Config.from_env({"ALPHA_VANTAGE_REQUEST_INTERVAL_SECONDS": "0.25"})

    assert config.alpha_vantage_request_interval_seconds == 0.25


def test_alpaca_market_data_feed_is_configurable():
    config = Config.from_env({"ALPACA_MARKET_DATA_FEED": "iex"})

    assert config.alpaca_market_data_feed == "iex"


def test_preflight_yfinance_import_failure_fails(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("synthetic missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    config = Config.from_env(
        {
            "ROBOT_WEALTH_API_KEY": "SYNTHETIC_RW_KEY",
            "OPENAI_API_KEY": "SYNTHETIC_OPENAI_KEY",
        }
    )

    ok, health = config.preflight()

    assert ok is False
    assert any(item.provider == "yfinance" and item.state == "error" for item in health)
