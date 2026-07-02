"""Domain contracts for report-only M&A blacklist governance."""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,15}$")


class ProviderState(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    ERROR = "error"
    STALE = "stale"


class EvidenceStrength(StrEnum):
    STRUCTURED = "structured"
    NEWS = "news"
    MARKET = "market"
    OPERATOR = "operator"
    LIFECYCLE = "lifecycle"


class InputStatus(StrEnum):
    BLOCKED = "blocked"
    SCANNER_CANDIDATE = "scanner_candidate"
    WATCHLIST_ONLY = "watchlist_only"
    HISTORICAL_WATCHLIST = "historical_watchlist"


class Recommendation(StrEnum):
    KEEP = "Keep"
    REMOVE = "Remove"
    PROMOTE = "Promote to Block"
    DO_NOT_PROMOTE = "Do Not Promote"


class Confidence(StrEnum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class WorkflowName(StrEnum):
    DISCOVER = "discover"
    PROMOTION_REVIEW = "promotion-review"
    DURABLE_EXIT_REVIEW = "durable-exit-review"


ALLOWED_RECOMMENDATIONS: dict[str, set[str]] = {
    InputStatus.BLOCKED.value: {Recommendation.KEEP.value, Recommendation.REMOVE.value},
    InputStatus.SCANNER_CANDIDATE.value: {
        Recommendation.PROMOTE.value,
        Recommendation.DO_NOT_PROMOTE.value,
    },
    InputStatus.WATCHLIST_ONLY.value: {
        Recommendation.PROMOTE.value,
        Recommendation.DO_NOT_PROMOTE.value,
    },
    InputStatus.HISTORICAL_WATCHLIST.value: {
        Recommendation.PROMOTE.value,
        Recommendation.DO_NOT_PROMOTE.value,
    },
}


def _normalize_required_ticker(value: str) -> str:
    ticker = value.strip().upper()
    if not ticker:
        raise ValueError("ticker is required")
    if not TICKER_PATTERN.fullmatch(ticker):
        raise ValueError("ticker must be a single symbol using letters, numbers, '.', '_', or '-'")
    return ticker


class ProviderHealth(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    provider: str
    state: ProviderState
    records: int = 0
    message: str | None = None


class PairContext(BaseModel):
    ticker: str
    peers: list[str] = Field(default_factory=list)
    status: Literal["in_universe", "out_of_universe", "missing"] = "missing"

    @property
    def current_pair_count(self) -> int | None:
        if self.status == "missing":
            return None
        return len(self.peers)


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    ticker: str
    provider: str
    source_type: str
    strength: EvidenceStrength
    source_date: str | None = None
    title: str | None = None
    summary: str | None = None
    url: str | None = None
    role_hint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DiscoveryCandidate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    ticker: str
    input_status: InputStatus
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    pair_context: PairContext
    already_durable: bool = False

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return _normalize_required_ticker(value)


class ProviderEvidenceCount(BaseModel):
    provider: str
    count: int


class GovernanceResult(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    ticker: str
    input_status: InputStatus
    recommendation: Recommendation
    ticker_role: str
    deal_stage: str
    confidence: Confidence
    key_evidence: list[str] = Field(min_length=1)
    reasoning: str
    provider_evidence_counts: list[ProviderEvidenceCount] = Field(default_factory=list)
    run_date: str
    affected_pair_count: int | None = None

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return _normalize_required_ticker(value)

    @field_validator("ticker_role", "deal_stage", "reasoning", "run_date")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("field must be populated")
        return value.strip()

    @model_validator(mode="after")
    def validate_recommendation_for_status(self) -> "GovernanceResult":
        allowed = ALLOWED_RECOMMENDATIONS[str(self.input_status)]
        if str(self.input_status) == InputStatus.BLOCKED.value:
            if str(self.recommendation) not in allowed:
                raise ValueError("blocked inputs allow only Keep or Remove")
        elif str(self.recommendation) not in allowed:
            raise ValueError("candidate inputs allow only Promote to Block or Do Not Promote")
        return self


class WorkflowArtifacts(BaseModel):
    discovery_path: str | None = None
    provider_health_path: str | None = None
    governance_results_path: str | None = None
    report_path: str | None = None


class WorkflowResult(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    workflow: WorkflowName
    run_id: str
    denominator_count: int
    selected_count: int
    openai_called: bool
    candidates: list[DiscoveryCandidate] = Field(default_factory=list)
    governance_results: list[GovernanceResult] = Field(default_factory=list)
    provider_health: list[ProviderHealth] = Field(default_factory=list)
    incomplete_coverage: bool = False
    artifacts: WorkflowArtifacts = Field(default_factory=WorkflowArtifacts)
    report_text: str = ""


def today_iso() -> str:
    return date.today().isoformat()
