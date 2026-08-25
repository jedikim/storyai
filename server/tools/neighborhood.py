from __future__ import annotations

from typing import Any

from server.runtime import get_service


def neighborhood(
    intent: str,
    anchors: list[str] | None = None,
    as_of: int | None = None,
    budget_tokens: int = 4_000,
) -> dict[str, Any]:
    """Build a token-budgeted context packet from search seeds and one-hop neighbors."""
    return get_service().neighborhood(
        intent,
        anchors=anchors,
        as_of=as_of,
        budget_tokens=budget_tokens,
    )
