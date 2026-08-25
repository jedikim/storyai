from __future__ import annotations

from typing import Any, Literal

from server.runtime import get_service


def lease(
    mode: Literal["acquire", "release", "list"],
    session_id: str,
    scope: str | None = None,
    ttl_sec: int = 900,
    model_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Acquire, release, or list advisory work-scope leases with mandatory TTL."""
    return get_service().lease(
        mode=mode,
        session_id=session_id,
        scope=scope,
        ttl_sec=ttl_sec,
        model_id=model_id,
        note=note,
    )
