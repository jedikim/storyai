"""Lazy process-local service lifecycle."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from .core.projects import ProjectMode, ProjectRegistry
from .core.service import StoryService


@lru_cache(maxsize=1)
def get_project_registry() -> ProjectRegistry:
    return ProjectRegistry.from_environment()


@lru_cache(maxsize=8)
def _service_for(root: str, database: str) -> StoryService:
    return StoryService.for_project(root, database)


def get_service() -> StoryService:
    project = get_project_registry().current()
    return _service_for(str(project["root"]), str(project["db"]))


def manage_project(
    *,
    mode: ProjectMode,
    name: str | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    registry = get_project_registry()
    previous = registry.selected_name() if mode in {"create", "register", "select"} else None
    result = registry.manage(mode=mode, name=name, path=path)
    if mode in {"create", "register", "select"}:
        try:
            service = get_service()
        except Exception:
            assert previous is not None
            registry.restore_selection(previous)
            raise
        result["graph"] = service.writer.graph_revision()
    return result


def reset_service() -> None:
    _service_for.cache_clear()
    get_project_registry.cache_clear()
