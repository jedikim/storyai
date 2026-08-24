from __future__ import annotations

from typing import Any, Literal

from server.runtime import get_service

ToolResponse = list[dict[str, Any]] | dict[str, Any]


def promises(
    status: list[str] | None = None,
    as_of: int | None = None,
    sort: Literal["debt", "age", "s_eff"] = "debt",
    response_format: Literal["concise", "detailed"] = "concise",
    max_chars: int = 32_000,
) -> ToolResponse:
    """List Promise F-T-P state, debt, salience, and coherence metrics."""
    return get_service().promises(
        status=status,
        as_of=as_of,
        sort=sort,
        response_format=response_format,
        max_chars=max_chars,
    )
