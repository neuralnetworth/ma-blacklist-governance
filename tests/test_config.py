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
