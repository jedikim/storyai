from __future__ import annotations

from typing import Any, Literal

from server.runtime import get_service


def commit(
    proposal_id: str,
    mode: Literal["apply", "dry_run"] = "apply",
) -> dict[str, Any]:
    """Atomically apply or dry-run one recorded proposal through the commit lane."""
    return get_service().commit(proposal_id, mode=mode)
