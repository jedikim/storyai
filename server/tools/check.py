from __future__ import annotations

from typing import Any, Literal

from server.runtime import get_service

ToolResponse = list[dict[str, Any]] | dict[str, Any]


def check(
    scope: str = "book",
    rules: list[str] | None = None,
    severity: Literal["error", "warn", "info"] | None = None,
    response_format: Literal["concise", "detailed"] = "concise",
    max_chars: int = 32_000,
) -> ToolResponse:
    """Run deterministic SQL continuity checks over a node scope or the book."""
    return get_service().check(
        scope,
        rules=rules,
        severity=severity,
        response_format=response_format,
        max_chars=max_chars,
    )
