"""Runtime configuration loaded from local environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .models import ProviderHealth, ProviderState


DEFAULT_ROBOT_WEALTH_API_BASE_URL = "https://api.robotwealth.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-5.5"


def _load_env_file(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Config:
    robot_wealth_api_key: str | None
    openai_api_key: str | None
    robot_wealth_api_base_url: str = DEFAULT_ROBOT_WEALTH_API_BASE_URL
    openai_model: str = DEFAULT_OPENAI_MODEL
    output_root: Path = Path("runs")
    alpaca_api_key: str | None = None
    alpaca_secret_key: str | None = None
    alpha_vantage_api_key: str | None = None

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        env_file: Path | None = None,
        output_root: Path | None = None,
    ) -> "Config":
        file_values = _load_env_file(env_file)
        merged = dict(os.environ if env is None else env)
        merged.update({k: v for k, v in file_values.items() if v})
        return cls(
            robot_wealth_api_key=merged.get("ROBOT_WEALTH_API_KEY") or None,
            openai_api_key=merged.get("OPENAI_API_KEY") or None,
            robot_wealth_api_base_url=merged.get("ROBOT_WEALTH_API_BASE_URL", DEFAULT_ROBOT_WEALTH_API_BASE_URL),
            openai_model=merged.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
            output_root=Path(output_root or merged.get("MA_BLACKLIST_OUTPUT_ROOT") or "runs"),
            alpaca_api_key=merged.get("ALPACA_API_KEY") or merged.get("ALPACA_API_KEY_ID") or None,
            alpaca_secret_key=merged.get("ALPACA_SECRET_KEY") or None,
            alpha_vantage_api_key=merged.get("ALPHA_VANTAGE_API_KEY") or None,
        )

    def secret_values(self) -> list[str]:
        return [
            value
            for value in [
                self.robot_wealth_api_key,
                self.openai_api_key,
                self.alpaca_api_key,
                self.alpaca_secret_key,
                self.alpha_vantage_api_key,
            ]
            if value
        ]

    def preflight(self) -> tuple[bool, list[ProviderHealth]]:
        health: list[ProviderHealth] = []
        ok = True
        for provider, value in [
            ("robot_wealth", self.robot_wealth_api_key),
            ("openai", self.openai_api_key),
        ]:
            if value:
                health.append(ProviderHealth(provider=provider, state=ProviderState.OK, message="configured"))
            else:
                ok = False
                health.append(ProviderHealth(provider=provider, state=ProviderState.ERROR, message="missing required credential"))

        try:
            import yfinance  # noqa: F401

            health.append(ProviderHealth(provider="yfinance", state=ProviderState.OK, message="importable"))
        except Exception as exc:  # pragma: no cover - environment dependent
            ok = False
            health.append(ProviderHealth(provider="yfinance", state=ProviderState.ERROR, message=f"dependency unavailable: {exc!r}"))

        rich_configured = bool(self.alpaca_api_key and self.alpaca_secret_key)
        health.append(
            ProviderHealth(
                provider="alpaca",
                state=ProviderState.OK if rich_configured else ProviderState.SKIPPED,
                message="configured" if rich_configured else "optional provider not configured",
            )
        )
        av_configured = bool(self.alpha_vantage_api_key)
        health.append(
            ProviderHealth(
                provider="alpha_vantage",
                state=ProviderState.OK if av_configured else ProviderState.SKIPPED,
                message="configured" if av_configured else "optional provider not configured",
            )
        )
        return ok, health
