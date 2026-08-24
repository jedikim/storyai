#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
project_dir="$(dirname -- "$script_dir")"

if [[ -x "$project_dir/.venv/bin/python" ]]; then
  exec "$project_dir/.venv/bin/python" -m storyai.server
fi

exec python3 -m storyai.server
