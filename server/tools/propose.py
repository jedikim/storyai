from __future__ import annotations

from typing import Any, Literal

from server.runtime import get_service


def propose(
    ops: list[dict[str, Any]],
    read_set: list[dict[str, Any]],
    rationale: str,
    session_id: str,
    actor_kind: Literal["human", "agent", "cascade"] = "agent",
    model_id: str | None = None,
    host: Literal["claude-code", "codex", "ui", "test"] = "codex",
    on_behalf_of: str | None = None,
) -> dict[str, Any]:
    """Record an atomic mutation proposal without changing live graph state."""
    return get_service().propose(
        ops=ops,
        read_set=read_set,
        rationale=rationale,
        session_id=session_id,
        actor_kind=actor_kind,
        model_id=model_id,
        host=host,
        on_behalf_of=on_behalf_of,
    )
