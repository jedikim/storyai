from __future__ import annotations

from typing import Any, Literal

from server.runtime import get_service


def ingest(
    chapter: str,
    mode: Literal["extract", "reindex"] = "extract",
) -> dict[str, Any]:
    """Validate a full chapter binding manifest and create, but never commit, its Proposal."""
    return get_service().ingest(chapter, mode=mode)
