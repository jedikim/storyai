"""CLI for processing queued P6 Tier-2 rederive jobs."""

from __future__ import annotations

import argparse
import json

from .core.rederive import CascadeWorker, WebhookRederiveProvider
from .runtime import get_service


def main() -> None:
    parser = argparse.ArgumentParser(description="Process storyai Tier-2 cascade jobs")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    service = get_service()
    worker = CascadeWorker(
        db_path=service.db_path,
        writer=service.writer,
        provider=WebhookRederiveProvider.from_environment(),
    )
    print(json.dumps(worker.run(limit=args.limit), ensure_ascii=False))


if __name__ == "__main__":
    main()
