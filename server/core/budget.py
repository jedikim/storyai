"""Response-budget degradation without hard truncation."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

DEFAULT_MAX_CHARS = 32_000
MIN_MAX_CHARS = 256
HARD_MAX_CHARS = 100_000


def serialized_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def fit_response(value: Any, max_chars: int = DEFAULT_MAX_CHARS) -> Any:
    """Return the richest response form that fits the caller's character budget."""
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise ValueError("max_chars는 정수여야 합니다")
    if max_chars < MIN_MAX_CHARS or max_chars > HARD_MAX_CHARS:
        raise ValueError(f"max_chars는 {MIN_MAX_CHARS}~{HARD_MAX_CHARS} 범위여야 합니다")
    if serialized_size(value) <= max_chars:
        return value
    items = _items(value)
    outline = _outline(items)
    if serialized_size(outline) <= max_chars:
        return outline
    address_map = _address_map(items)
    if serialized_size(address_map) <= max_chars:
        return address_map
    counts = Counter(str(item.get("kind", "unknown")) for item in items if isinstance(item, dict))
    return {
        "counts": dict(sorted(counts.items())),
        "guidance": "결과가 너무 넓습니다. kind, as_of, scope 또는 limit으로 범위를 좁히세요.",
    }


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "nodes", "results"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
        flattened: list[dict[str, Any]] = []
        for key, child in value.items():
            if isinstance(child, list):
                for item in child:
                    if isinstance(item, dict):
                        flattened.append(item | {"kind": item.get("kind", key)})
        return flattened
    return []


def _outline(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("id", "kind", "title", "summary", "score", "rel", "hard", "story_range")
    return [{key: item[key] for key in keys if key in item} for item in items]


def _address_map(items: list[dict[str, Any]]) -> dict[str, list[str]]:
    result: defaultdict[str, list[str]] = defaultdict(list)
    for item in items:
        node_id = item.get("id")
        if not isinstance(node_id, str):
            continue
        kind = item.get("kind")
        if not isinstance(kind, str):
            kind = node_id.split("/", 1)[0]
        result[kind].append(node_id)
    return {kind: sorted(set(ids)) for kind, ids in sorted(result.items())}
