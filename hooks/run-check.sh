#!/usr/bin/env bash
# 두 호스트의 훅이 공통으로 부르는 스크립트.
# 규칙 로직은 여기 없습니다 — server/core 안에 한 번만 존재하고, 이건 트리거일 뿐입니다.
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo .)"
exec python3 -m storyai.check --scope "${1:-book}" --severity error --format json
