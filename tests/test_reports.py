from ma_blacklist_governance.models import (
    GovernanceResult,
    ProviderHealth,
    ProviderState,
    WorkflowName,
    WorkflowResult,
)
from ma_blacklist_governance.reports import render_report
import pytest


def test_report_includes_provider_gap_and_redacts_secret():
    result = WorkflowResult(
        workflow=WorkflowName.PROMOTION_REVIEW,
        run_id="run1",
        denominator_count=1,
        selected_count=1,
        openai_called=True,
        provider_health=[
            ProviderHealth(provider="alpha_vantage", state=ProviderState.SKIPPED, message="missing SYNTHETIC_OPENAI_KEY ![x](https://example.test)")
        ],
        governance_results=[
            GovernanceResult(
                ticker="AAA",
                input_status="watchlist_only",
                recommendation="Do Not Promote",
                ticker_role="Evidence-limited / unknown",
                deal_stage="No Evidence Found",
                confidence="Low",
                key_evidence=["<script>alert('x')</script> ![x](https://example.test/x.png)"],
                reasoning="No corroborating evidence.",
                run_date="2026-01-16",
            )
        ],
    )

    text = render_report(result, ["SYNTHETIC_OPENAI_KEY"])

    assert "alpha_vantage: skipped" in text
    assert "SYNTHETIC_OPENAI_KEY" not in text
    assert "&lt;script&gt;" in text
    assert "\\!\\[x\\]" in text


def test_no_candidates_report_states_no_full_universe_governance():
    result = WorkflowResult(
        workflow=WorkflowName.PROMOTION_REVIEW,
        run_id="run2",
        denominator_count=5,
        selected_count=0,
        openai_called=False,
    )

    text = render_report(result)

    assert "Review denominator: 5" in text
    assert "Governance was not run over the full Robot Wealth universe" in text


def test_incomplete_coverage_changes_no_candidates_wording():
    result = WorkflowResult(
        workflow=WorkflowName.PROMOTION_REVIEW,
        run_id="run3",
        denominator_count=0,
        selected_count=0,
        openai_called=False,
        incomplete_coverage=True,
        provider_health=[ProviderHealth(provider="robot_wealth", state=ProviderState.ERROR, message="synthetic outage")],
    )

    text = render_report(result)

    assert "Incomplete Coverage" in text
    assert "Candidate discovery did not complete" in text
    assert "No deterministic discovery candidates were found" not in text


def test_invalid_ticker_markdown_is_rejected_before_report():
    with pytest.raises(ValueError):
        GovernanceResult(
            ticker="![X](HTTPS://EXAMPLE.TEST)",
            input_status="watchlist_only",
            recommendation="Do Not Promote",
            ticker_role="Evidence-limited / unknown",
            deal_stage="No Evidence Found",
            confidence="Low",
            key_evidence=["synthetic"],
            reasoning="synthetic",
            run_date="2026-01-16",
        )
