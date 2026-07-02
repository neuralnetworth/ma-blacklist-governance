"""Markdown report rendering."""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from .models import GovernanceResult, ProviderHealth, ProviderState, Recommendation, WorkflowResult
from .security import redact_text, sanitize_markdown_evidence


def _provider_notes(health: Sequence[ProviderHealth]) -> list[str]:
    notes = []
    for provider in health:
        if provider.state != ProviderState.OK.value:
            provider_name = sanitize_markdown_evidence(provider.provider)
            provider_state = sanitize_markdown_evidence(str(provider.state))
            msg = f": {sanitize_markdown_evidence(provider.message)}" if provider.message else ""
            notes.append(f"- {provider_name}: {provider_state}{msg}")
    return notes


def _counts(results: Sequence[GovernanceResult]) -> Counter:
    return Counter(result.recommendation for result in results)


def render_report(result: WorkflowResult, secrets: Sequence[str] | None = None) -> str:
    lines: list[str] = [f"# M&A Blacklist Governance Report - {result.workflow}", ""]
    lines.extend(
        [
            "## Summary",
            f"- Run ID: `{result.run_id}`",
            f"- Review denominator: {result.denominator_count}",
            f"- Tickers selected for governance: {result.selected_count}",
            f"- OpenAI governance called: {'yes' if result.openai_called else 'no'}",
        ]
    )
    counts = _counts(result.governance_results)
    for name in [
        Recommendation.PROMOTE.value,
        Recommendation.DO_NOT_PROMOTE.value,
        Recommendation.KEEP.value,
        Recommendation.REMOVE.value,
    ]:
        if counts.get(name):
            lines.append(f"- {name}: {counts[name]}")

    notes = _provider_notes(result.provider_health)
    if notes:
        lines.extend(["", "## Provider And Source Notes", *notes])

    if result.incomplete_coverage:
        lines.extend(
            [
                "",
                "## Incomplete Coverage",
                "At least one required provider failed. Treat this report as incomplete until provider errors are resolved.",
            ]
        )

    if not result.candidates and not result.governance_results:
        lines.extend(
            [
                "",
                "## No Candidates" if not result.incomplete_coverage else "## No Complete Candidate Set",
                (
                    "No deterministic discovery candidates were found. Governance was not run over the full Robot Wealth universe as a fallback."
                    if not result.incomplete_coverage
                    else "Candidate discovery did not complete because required provider coverage failed."
                ),
            ]
        )
        return redact_text("\n".join(lines) + "\n", secrets)

    if result.governance_results:
        lines.extend(["", "## Recommendations"])
        for item in result.governance_results:
            lines.extend(
                [
                    "",
                    f"### {sanitize_markdown_evidence(item.ticker, secrets)} - {item.recommendation}",
                    f"- Input status: `{item.input_status}`",
                    f"- Ticker role: {sanitize_markdown_evidence(item.ticker_role, secrets)}",
                    f"- Deal stage: {sanitize_markdown_evidence(item.deal_stage, secrets)}",
                    f"- Confidence: {item.confidence}",
                    f"- Affected pair count: {item.affected_pair_count if item.affected_pair_count is not None else 'Unknown'}",
                    "",
                    "**Key evidence:**",
                ]
            )
            for evidence in item.key_evidence:
                lines.append(f"- {sanitize_markdown_evidence(evidence, secrets)}")
            lines.extend(["", f"**Reasoning:** {sanitize_markdown_evidence(item.reasoning, secrets)}"])
    else:
        lines.extend(["", "## Discovery Candidates"])
        for candidate in result.candidates:
            ticker = sanitize_markdown_evidence(candidate.ticker, secrets)
            reasons = sanitize_markdown_evidence(", ".join(candidate.reasons), secrets)
            lines.append(f"- {ticker}: {reasons}")

    lines.extend(
        [
            "",
            "## Report-Only Boundary",
            "These are review recommendations only. No command in this run edited a live blacklist file.",
        ]
    )
    return redact_text("\n".join(lines) + "\n", secrets)
