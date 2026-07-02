"""Validation helpers for governance model output."""

from __future__ import annotations

from typing import Any

from .models import GovernanceResult


def validate_governance_result(payload: dict[str, Any] | GovernanceResult) -> GovernanceResult:
    """Validate schema and domain vocabulary for a governance result."""

    if isinstance(payload, GovernanceResult):
        return payload
    return GovernanceResult.model_validate(payload)


def governance_json_schema() -> dict[str, Any]:
    """Return the JSON Schema sent to OpenAI structured outputs."""

    return _strict_json_schema(GovernanceResult.model_json_schema())


def _strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Adapt Pydantic JSON Schema to the strict subset OpenAI accepts."""

    def convert(node: Any) -> Any:
        if isinstance(node, list):
            return [convert(item) for item in node]
        if not isinstance(node, dict):
            return node
        converted = {key: convert(value) for key, value in node.items()}
        converted.pop("default", None)
        if converted.get("type") == "object" or "properties" in converted:
            properties = converted.get("properties") or {}
            converted["additionalProperties"] = False
            converted["required"] = list(properties.keys())
        return converted

    return convert(schema)
