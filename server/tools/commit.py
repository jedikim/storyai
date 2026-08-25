from __future__ import annotations

from typing import Any, Literal

from server.runtime import get_service


def commit(
    proposal_id: str,
    mode: Literal["apply", "dry_run"] = "apply",
    allow_cycles: bool = False,
    max_iterations: int | None = None,
) -> dict[str, Any]:
    """Atomically apply or dry-run one recorded proposal through the commit lane."""
    return get_service().commit(
        proposal_id,
        mode=mode,
        allow_cycles=allow_cycles,
        max_iterations=max_iterations,
    )
