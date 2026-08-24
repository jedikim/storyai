"""Lazy process-local service lifecycle."""

from __future__ import annotations

from functools import lru_cache

from .core.service import StoryService


@lru_cache(maxsize=1)
def get_service() -> StoryService:
    return StoryService.from_environment()


def reset_service() -> None:
    get_service.cache_clear()
