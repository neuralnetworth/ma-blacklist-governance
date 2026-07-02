"""Robot Wealth universe adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from ..config import Config
from ..models import PairContext, ProviderHealth, ProviderState
from ..security import redact_text


@dataclass(frozen=True)
class UniverseResult:
    tickers: list[str]
    pair_context: dict[str, PairContext]
    provider_health: ProviderHealth


def _load_rows_from_fixture(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    rows = data.get("rows", [])
    if not isinstance(rows, list):
        raise ValueError("Robot Wealth fixture must be a list or contain rows[]")
    return rows


def _fetch_rows(config: Config, *, limit: int = 50_000, timeout: float = 30.0) -> list[dict]:
    if not config.robot_wealth_api_key:
        raise ValueError("missing ROBOT_WEALTH_API_KEY")
    base = config.robot_wealth_api_base_url.rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or parsed.hostname != "api.robotwealth.com":
        raise ValueError("ROBOT_WEALTH_API_BASE_URL must be https://api.robotwealth.com/v1 before sending credentials")
    params = urlencode({"api_key": config.robot_wealth_api_key, "limit": limit})
    url = f"{base}/equities/statarb/hourly/topspreads?{params}"
    req = Request(url, method="GET")
    with urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        raise ValueError("Robot Wealth response rows is not a list")
    return rows


def _symbols_from_row(row: dict) -> tuple[str, str] | None:
    stock1 = str(row.get("stock1") or row.get("ticker") or "").strip().upper()
    stock2 = str(row.get("stock2") or "").strip().upper()
    if not stock1 or not stock2:
        return None
    return stock1, stock2


def build_pair_context(rows: list[dict]) -> tuple[list[str], dict[str, PairContext], int]:
    peers: dict[str, set[str]] = {}
    invalid_rows = 0
    for row in rows:
        if not isinstance(row, dict):
            invalid_rows += 1
            continue
        symbols = _symbols_from_row(row)
        if symbols is None:
            invalid_rows += 1
            continue
        stock1, stock2 = symbols
        peers.setdefault(stock1, set()).add(stock2)
        peers.setdefault(stock2, set()).add(stock1)
    tickers = sorted(peers)
    context = {
        ticker: PairContext(ticker=ticker, peers=sorted(peer_set), status="in_universe")
        for ticker, peer_set in peers.items()
    }
    return tickers, context, invalid_rows


def load_universe(config: Config, fixture_path: Path | None = None) -> UniverseResult:
    try:
        rows = _load_rows_from_fixture(fixture_path) if fixture_path else _fetch_rows(config)
        tickers, context, invalid_rows = build_pair_context(rows)
        message = f"{len(tickers)} unique tickers"
        if invalid_rows:
            message = f"{message}; {invalid_rows} malformed rows skipped"
        return UniverseResult(
            tickers=tickers,
            pair_context=context,
            provider_health=ProviderHealth(
                provider="robot_wealth",
                state=ProviderState.OK,
                records=len(rows),
                message=message,
            ),
        )
    except (HTTPError, URLError, ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        msg = redact_text(str(exc), config.secret_values())
        return UniverseResult(
            tickers=[],
            pair_context={},
            provider_health=ProviderHealth(provider="robot_wealth", state=ProviderState.ERROR, message=msg),
        )
