from __future__ import annotations

from typing import Any

from server.runtime import get_service


def impact(
    ref: str,
    change: dict[str, Any],
    max_depth: int = 3,
) -> dict[str, Any]:
    """Preview upstream dependents and continuity rules affected by a hypothetical change."""
    return get_service().impact(ref, change=change, max_depth=max_depth)
