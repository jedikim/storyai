from __future__ import annotations

from typing import Any, Literal

from server.runtime import manage_project


def project(
    mode: Literal["current", "list", "create", "register", "select"],
    name: str | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    """Create, register, select, or inspect isolated story projects."""
    return manage_project(mode=mode, name=name, path=path)
