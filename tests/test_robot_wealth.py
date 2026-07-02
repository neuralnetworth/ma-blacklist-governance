from ma_blacklist_governance.config import Config
from ma_blacklist_governance.providers.robot_wealth import load_universe


def test_malformed_robot_wealth_rows_are_counted(tmp_path):
    fixture = tmp_path / "rw.json"
    fixture.write_text('{"rows":[null, {"ticker":"AAA"}, {"ticker":"AAA","stock2":"BBB"}]}', encoding="utf-8")

    result = load_universe(Config.from_env({}), fixture)

    assert result.tickers == ["AAA", "BBB"]
    assert "2 malformed rows skipped" in result.provider_health.message


def test_robot_wealth_rejects_unsafe_base_url_before_sending_key():
    config = Config.from_env(
        {
            "ROBOT_WEALTH_API_KEY": "SYNTHETIC_RW_KEY",
            "ROBOT_WEALTH_API_BASE_URL": "http://example.test/v1",
        }
    )

    result = load_universe(config)

    assert result.provider_health.state == "error"
    assert "https://api.robotwealth.com/v1" in result.provider_health.message
