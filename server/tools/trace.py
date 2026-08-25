from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from server.runtime import get_service


def trace(
    from_: Annotated[str, Field(alias="from")],
    to: str | None = None,
    via: list[str] | None = None,
    max_depth: int = 5,
    k: int = 5,
) -> list[dict[str, Any]]:
    """Return bounded hard-edge paths from one node to a target or narrative device."""
    return get_service().trace(from_, target=to, via=via, max_depth=max_depth, k=k)
