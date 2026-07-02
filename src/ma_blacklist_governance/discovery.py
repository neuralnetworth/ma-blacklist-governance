"""Deterministic discovery assembly."""

from __future__ import annotations

from collections import defaultdict

from .models import DiscoveryCandidate, EvidenceRecord, EvidenceStrength, InputStatus, PairContext


def context_for_ticker(ticker: str, pair_context: dict[str, PairContext]) -> PairContext:
    ticker = ticker.upper()
    return pair_context.get(ticker) or PairContext(ticker=ticker, peers=[], status="out_of_universe")


def candidates_from_evidence(
    evidence: list[EvidenceRecord],
    pair_context: dict[str, PairContext],
    durable_tickers: set[str] | None = None,
) -> list[DiscoveryCandidate]:
    durable = {ticker.upper() for ticker in durable_tickers or set()}
    grouped: dict[str, list[EvidenceRecord]] = defaultdict(list)
    for record in evidence:
        grouped[record.ticker.upper()].append(record)

    candidates: list[DiscoveryCandidate] = []
    for ticker, records in sorted(grouped.items()):
        already_durable = ticker in durable
        strongest = {record.strength for record in records}
        if already_durable:
            status = InputStatus.BLOCKED
        elif EvidenceStrength.STRUCTURED in strongest or "structured" in strongest:
            status = InputStatus.SCANNER_CANDIDATE
        else:
            status = InputStatus.WATCHLIST_ONLY
        reasons = sorted({record.source_type for record in records})
        candidates.append(
            DiscoveryCandidate(
                ticker=ticker,
                input_status=status,
                evidence=records,
                reasons=reasons,
                pair_context=context_for_ticker(ticker, pair_context),
                already_durable=already_durable,
            )
        )
    return candidates


def add_operator_candidates(
    tickers: list[str],
    existing: list[DiscoveryCandidate],
    pair_context: dict[str, PairContext],
) -> list[DiscoveryCandidate]:
    by_ticker = {candidate.ticker: candidate for candidate in existing}
    for ticker in tickers:
        normalized = ticker.strip().upper()
        if not normalized or normalized in by_ticker:
            continue
        evidence = [
            EvidenceRecord(
                ticker=normalized,
                provider="operator",
                source_type="operator_supplied",
                strength=EvidenceStrength.OPERATOR,
                summary="Operator supplied candidate for promotion review.",
            )
        ]
        by_ticker[normalized] = DiscoveryCandidate(
            ticker=normalized,
            input_status=InputStatus.WATCHLIST_ONLY,
            evidence=evidence,
            reasons=["operator_supplied"],
            pair_context=context_for_ticker(normalized, pair_context),
        )
    return [by_ticker[key] for key in sorted(by_ticker)]
