"""Command-line entry point for P3 offline search-index consolidation."""

from __future__ import annotations

import json

from .runtime import get_service


def main() -> None:
    result = get_service().consolidate()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
