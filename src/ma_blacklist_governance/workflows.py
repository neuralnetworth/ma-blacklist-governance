"""Workflow orchestration and artifact writing."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Iterable

from .config import Config
from .discovery import add_operator_candidates, candidates_from_evidence, context_for_ticker
from .models import (
    DiscoveryCandidate,
    EvidenceRecord,
    EvidenceStrength,
    InputStatus,
    PairContext,
    ProviderHealth,
    ProviderState,
    WorkflowArtifacts,
    WorkflowName,
    WorkflowResult,
    today_iso,
)
from .providers.openai_governance import GovernanceClient, OfflineGovernanceClient
from .providers.rich import collect_alpaca_market_evidence, collect_rich_evidence
from .providers.robot_wealth import load_universe
from .providers.yfinance_provider import collect_market_evidence, collect_news_evidence, load_market_fixture, load_news_fixture
from .reports import render_report
from .security import redacted_json, redact_text


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
RUN_MARKER = ".ma_blacklist_governance_run"


def make_run_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%d_%H%M%S_%f")


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id) or run_id in {".", ".."}:
        raise ValueError("run_id must be a single path segment containing only letters, numbers, '_', '.', or '-'")
    if Path(run_id).is_absolute() or "/" in run_id or "\\" in run_id:
        raise ValueError("run_id must not contain path separators")
    return run_id


def load_ticker_file(path: Path | None) -> list[str]:
    if path is None:
        return []
    return parse_ticker_text(path.read_text(encoding="utf-8"))


def parse_ticker_text(text: str) -> list[str]:
    tickers: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().upper()
        if line:
            tickers.append(line)
    return tickers


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _write_text(path: Path, text: str, secrets: list[str]) -> None:
    _atomic_write(path, redact_text(text, secrets))


def _write_json(path: Path, data: object, secrets: list[str]) -> None:
    _atomic_write(path, redacted_json(data, secrets))


def _artifact_dir(config: Config, workflow: WorkflowName, run_id: str, output_root: Path | None = None) -> Path:
    return (output_root or config.output_root) / validate_run_id(run_id) / workflow.value


def _prepare_artifact_dir(out_dir: Path) -> None:
    run_dir = out_dir.parent
    marker = run_dir / RUN_MARKER
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"artifact directory already exists: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    marker.write_text("created_by=ma-blacklist-governance\n", encoding="utf-8")


def _model_list_json(items: Iterable) -> list[dict]:
    return [item.model_dump(mode="json") for item in items]


@dataclass(frozen=True)
class DiscoveryBuild:
    denominator_count: int
    candidates: list[DiscoveryCandidate]
    provider_health: list[ProviderHealth]
    pair_context: dict[str, PairContext]
    incomplete_coverage: bool = False


def _build_discovery(
    config: Config,
    *,
    universe_json: Path | None,
    news_json: Path | None,
    rich_evidence_json: Path | None,
    market_json: Path | None,
    durable_tickers: Iterable[str] = (),
    attach_market: bool = True,
) -> DiscoveryBuild:
    universe = load_universe(config, universe_json)
    health: list[ProviderHealth] = [universe.provider_health]
    news_fixture = load_news_fixture(news_json) if news_json else None
    news_evidence, news_health = collect_news_evidence(universe.tickers, news_fixture)
    rich_evidence, rich_health = collect_rich_evidence(config, rich_evidence_json, tickers=universe.tickers)
    evidence = [*news_evidence, *rich_evidence]
    health.extend([news_health, *rich_health])
    candidates = candidates_from_evidence(evidence, universe.pair_context, set(durable_tickers))
    if attach_market:
        health.extend(_attach_market_evidence(config, candidates, market_json))
    return DiscoveryBuild(
        denominator_count=len(universe.tickers),
        candidates=candidates,
        provider_health=health,
        pair_context=universe.pair_context,
        incomplete_coverage=_has_required_provider_error(health),
    )


def _has_required_provider_error(health: Iterable[ProviderHealth]) -> bool:
    required = {"robot_wealth", "yfinance_news"}
    return any(item.provider in required and item.state == ProviderState.ERROR.value for item in health)


def _event_dates_by_ticker(candidates: list[DiscoveryCandidate]) -> dict[str, list[str]]:
    dates: dict[str, list[str]] = {}
    for candidate in candidates:
        ticker_dates = sorted(
            {
                parsed
                for record in candidate.evidence
                if record.source_date and record.source_type != "market_data"
                for parsed in [_source_date_key(str(record.source_date))]
                if parsed
            }
        )
        if ticker_dates:
            dates[candidate.ticker] = ticker_dates
    return dates


def _source_date_key(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return text


def _attach_market_evidence(config: Config, candidates: list[DiscoveryCandidate], market_json: Path | None) -> list[ProviderHealth]:
    existing_count = sum(
        1
        for candidate in candidates
        if any(record.provider == "yfinance" and record.source_type == "market_data" for record in candidate.evidence)
    )
    candidates_without_market = [
        candidate
        for candidate in candidates
        if not any(record.provider == "yfinance" and record.source_type == "market_data" for record in candidate.evidence)
    ]
    market_fixture = load_market_fixture(market_json) if market_json else None
    event_dates = _event_dates_by_ticker(candidates)
    market_evidence, market_health = collect_market_evidence(
        [candidate.ticker for candidate in candidates_without_market],
        market_fixture,
        event_dates,
    )
    if market_evidence:
        by_ticker = {candidate.ticker: candidate for candidate in candidates}
        for record in market_evidence:
            candidate = by_ticker.get(record.ticker)
            if candidate:
                candidate.evidence.append(record)
    if existing_count:
        market_health.records += existing_count
        market_health.state = ProviderState.OK
        market_health.message = f"{market_health.records} market snapshots"
    existing_alpaca_count = sum(
        1
        for candidate in candidates
        if any(record.provider == "alpaca_market_data" and record.source_type == "market_data" for record in candidate.evidence)
    )
    alpaca_candidates = [
        candidate
        for candidate in candidates
        if not any(record.provider == "alpaca_market_data" and record.source_type == "market_data" for record in candidate.evidence)
    ]
    alpaca_evidence, alpaca_health = collect_alpaca_market_evidence(
        config,
        [candidate.ticker for candidate in alpaca_candidates],
        event_dates_by_ticker=event_dates,
    )
    if alpaca_evidence:
        by_ticker = {candidate.ticker: candidate for candidate in candidates}
        for record in alpaca_evidence:
            candidate = by_ticker.get(record.ticker)
            if candidate:
                candidate.evidence.append(record)
    if existing_alpaca_count:
        alpaca_health.records += existing_alpaca_count
        alpaca_health.state = ProviderState.OK
        alpaca_health.message = f"{alpaca_health.records} records"
    return [market_health, alpaca_health]


def _run_governance(
    candidates: list[DiscoveryCandidate],
    health: list[ProviderHealth],
    client: GovernanceClient,
    run_date: str,
    secrets: list[str],
) -> list:
    governance_results = []
    for candidate in candidates:
        try:
            governance_results.append(client.review(candidate, health, run_date))
        except Exception as exc:
            health.append(
                ProviderHealth(
                    provider=f"openai:{candidate.ticker}",
                    state=ProviderState.ERROR,
                    message=redact_text(repr(exc), secrets),
                )
            )
    return governance_results


def discover(
    config: Config,
    *,
    universe_json: Path | None = None,
    news_json: Path | None = None,
    rich_evidence_json: Path | None = None,
    market_json: Path | None = None,
    durable_tickers: Iterable[str] = (),
    output_root: Path | None = None,
    run_id: str | None = None,
) -> WorkflowResult:
    run_id = run_id or make_run_id()
    out_dir = _artifact_dir(config, WorkflowName.DISCOVER, run_id, output_root)
    build = _build_discovery(
        config,
        universe_json=universe_json,
        news_json=news_json,
        rich_evidence_json=rich_evidence_json,
        market_json=market_json,
        durable_tickers=durable_tickers,
    )

    result = WorkflowResult(
        workflow=WorkflowName.DISCOVER,
        run_id=run_id,
        denominator_count=build.denominator_count,
        selected_count=len(build.candidates),
        openai_called=False,
        candidates=build.candidates,
        provider_health=build.provider_health,
        incomplete_coverage=build.incomplete_coverage,
    )
    result.report_text = render_report(result, config.secret_values())
    result.artifacts = _write_workflow_artifacts(config, out_dir, result, write_governance=False)
    return result


def promotion_review(
    config: Config,
    *,
    governance_client: GovernanceClient | None = None,
    operator_candidates: Iterable[str] = (),
    durable_tickers: Iterable[str] = (),
    universe_json: Path | None = None,
    news_json: Path | None = None,
    rich_evidence_json: Path | None = None,
    market_json: Path | None = None,
    output_root: Path | None = None,
    run_id: str | None = None,
) -> WorkflowResult:
    run_id = run_id or make_run_id()
    out_dir = _artifact_dir(config, WorkflowName.PROMOTION_REVIEW, run_id, output_root)
    durable = {ticker.strip().upper() for ticker in durable_tickers if ticker.strip()}
    build = _build_discovery(
        config,
        universe_json=universe_json,
        news_json=news_json,
        rich_evidence_json=rich_evidence_json,
        market_json=market_json,
        durable_tickers=durable,
        attach_market=False,
    )
    operator_list = [ticker for ticker in operator_candidates if ticker.strip().upper() not in durable]
    candidates = [candidate for candidate in build.candidates if not candidate.already_durable]
    if operator_list:
        candidates = add_operator_candidates(operator_list, build.candidates, build.pair_context)
        candidates = [candidate for candidate in candidates if not candidate.already_durable]
    build.provider_health.extend(_attach_market_evidence(config, candidates, market_json))

    client = governance_client or OfflineGovernanceClient()
    run_date = today_iso()
    governance_results = _run_governance(candidates, build.provider_health, client, run_date, config.secret_values())

    result = WorkflowResult(
        workflow=WorkflowName.PROMOTION_REVIEW,
        run_id=run_id,
        denominator_count=build.denominator_count,
        selected_count=len(candidates),
        openai_called=bool(candidates),
        candidates=candidates,
        governance_results=governance_results,
        provider_health=build.provider_health,
        incomplete_coverage=build.incomplete_coverage or _has_required_provider_error(build.provider_health),
    )
    result.report_text = render_report(result, config.secret_values())
    result.artifacts = _write_workflow_artifacts(config, out_dir, result, write_governance=bool(governance_results))
    return result


def durable_exit_review(
    config: Config,
    *,
    blacklist_file: Path,
    governance_client: GovernanceClient | None = None,
    universe_json: Path | None = None,
    news_json: Path | None = None,
    rich_evidence_json: Path | None = None,
    market_json: Path | None = None,
    output_root: Path | None = None,
    run_id: str | None = None,
) -> WorkflowResult:
    run_id = run_id or make_run_id()
    out_dir = _artifact_dir(config, WorkflowName.DURABLE_EXIT_REVIEW, run_id, output_root)
    before = blacklist_file.read_text(encoding="utf-8")
    tickers = parse_ticker_text(before)
    universe = load_universe(config, universe_json)
    news_fixture = load_news_fixture(news_json) if news_json else None
    news_evidence, news_health = collect_news_evidence(tickers, news_fixture)
    rich_evidence, rich_health = collect_rich_evidence(config, rich_evidence_json, tickers=tickers)
    health = [universe.provider_health, news_health, *rich_health]
    evidence_by_ticker: dict[str, list[EvidenceRecord]] = {ticker: [] for ticker in tickers}
    for record in [*news_evidence, *rich_evidence]:
        evidence_by_ticker.setdefault(record.ticker, []).append(record)
    candidates: list[DiscoveryCandidate] = []
    for ticker in tickers:
        evidence = evidence_by_ticker.get(ticker) or [
            EvidenceRecord(
                ticker=ticker,
                provider="operator",
                source_type="durable_blacklist",
                strength=EvidenceStrength.OPERATOR,
                summary="Operator supplied active durable blacklist ticker.",
            )
        ]
        candidates.append(
            DiscoveryCandidate(
                ticker=ticker,
                input_status=InputStatus.BLOCKED,
                evidence=evidence,
                reasons=["durable_blacklist"],
                pair_context=context_for_ticker(ticker, universe.pair_context),
                already_durable=True,
            )
        )
    health.extend(_attach_market_evidence(config, candidates, market_json))

    client = governance_client or OfflineGovernanceClient()
    run_date = today_iso()
    governance_results = _run_governance(candidates, health, client, run_date, config.secret_values())
    after = blacklist_file.read_text(encoding="utf-8")
    if before != after:
        raise RuntimeError("durable-exit-review mutated the input blacklist file")

    result = WorkflowResult(
        workflow=WorkflowName.DURABLE_EXIT_REVIEW,
        run_id=run_id,
        denominator_count=len(tickers),
        selected_count=len(candidates),
        openai_called=bool(candidates),
        candidates=candidates,
        governance_results=governance_results,
        provider_health=health,
        incomplete_coverage=_has_required_provider_error(health),
    )
    result.report_text = render_report(result, config.secret_values())
    result.artifacts = _write_workflow_artifacts(config, out_dir, result, write_governance=True)
    return result


def _write_workflow_artifacts(config: Config, out_dir: Path, result: WorkflowResult, *, write_governance: bool) -> WorkflowArtifacts:
    secrets = config.secret_values()
    _prepare_artifact_dir(out_dir)
    discovery_path = out_dir / "discovery.json"
    provider_health_path = out_dir / "provider_health.json"
    report_path = out_dir / "report.md"
    _write_json(discovery_path, _model_list_json(result.candidates), secrets)
    _write_json(provider_health_path, _model_list_json(result.provider_health), secrets)
    governance_path = None
    if write_governance:
        governance_path = out_dir / "governance_results.json"
        _write_json(governance_path, _model_list_json(result.governance_results), secrets)
    _write_text(report_path, result.report_text, secrets)
    return WorkflowArtifacts(
        discovery_path=str(discovery_path),
        provider_health_path=str(provider_health_path),
        governance_results_path=str(governance_path) if governance_path else None,
        report_path=str(report_path),
    )


def prune_runs(output_root: Path, older_than_days: int, *, now: datetime | None = None) -> list[Path]:
    resolved = output_root.resolve()
    dangerous = {Path("/").resolve(), Path.home().resolve(), Path.cwd().resolve()}
    if resolved in dangerous:
        raise ValueError(f"refusing to prune dangerous output root: {output_root}")
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=older_than_days)
    removed: list[Path] = []
    if not output_root.exists():
        return removed
    for child in output_root.iterdir():
        if not child.is_dir():
            continue
        if not (child / RUN_MARKER).exists():
            continue
        mtime = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
        if mtime <= cutoff:
            shutil.rmtree(child)
            removed.append(child)
    return removed
