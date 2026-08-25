from __future__ import annotations

from typing import Any

from server.runtime import get_service


def query(
    sql: str,
    params: dict[str, Any] | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Execute one bounded, parameterized SELECT or WITH query against the graph."""
    return get_service().query(sql, params=params, limit=limit)
