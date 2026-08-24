---
name: story-draft
description: 서사 그래프를 근거로 소설의 새 씬이나 챕터 초고를 씁니다. 회수 가능한 복선을 확인하고, 씬의 선행 조건을 검증하고, 인물의 인지 범위를 지킨 채 집필한 뒤 변경을 제안으로 올립니다. 사용자가 "다음 씬 써줘", "N장 초고", "이어서 써줘"라고 할 때 사용하세요.
license: MIT
compatibility: storyai MCP 서버가 연결되어 있어야 합니다.
allowed-tools: mcp__storyai__outline mcp__storyai__get mcp__storyai__neighborhood mcp__storyai__promises mcp__storyai__refs mcp__storyai__propose
metadata:
  short-description: 그래프 근거 초고 집필
---

# 초고 집필

## 1. 상태 파악

```
promises(status=["eligible"])        # 지금 회수할 수 있는 복선
get("story://session/latest")        # 이전 턴이 남긴 open_threads
outline(scope="<직전 챕터>", depth=1) # 어디서 끊겼는지
```

## 2. 컨텍스트 확보

`neighborhood(intent="<쓰려는 씬의 한 줄 요약>", as_of=<현재 장>, budget_tokens=4000)`

전문을 통째로 부르지 마세요. 정말 필요한 씬 하나만 `get(include="body")`로 가져옵니다.

## 3. 집필 전 검증

- 이 씬의 `pre` 조건이 현재 상태에서 만족되는가
- 등장 인물이 이 시점에 이 장소에 있을 수 있는가 (`present_at` 구간 확인)
- 인물이 발화하려는 사실이 그 인물에게 보이는가 (`visible_to`)
- `forbid`에 걸린 것을 노출하려 하지 않는가

하나라도 어긋나면 **집필하지 말고 사용자에게 알리세요.**

## 4. 집필

- 회수하는 복선은 `eligible`인 것만. `hypothetical`을 회수하면 근거 없는 해결입니다.
- 새로 심는 복선이 있으면 그것도 기록 대상입니다.
- 원고는 `manuscript/` 아래 해당 파일에 씁니다.

## 5. 제안 올리기

```
propose(
  ops=[씬 노드 추가, 복선 상태 변경, 새 복선 추가, 인물 상태 변경],
  read_set=[근거로 읽은 모든 노드와 그 rev],
  rationale="무엇을 왜 바꿨는지 한 문단"
)
```

`read_set`을 비우지 마세요. 충돌 판정과 변경 전파가 전부 그 필드에 걸려 있습니다.

## 6. 세션 마무리

`session/` 노드를 남깁니다. `open_threads`에 **끝내지 못한 것**을,
`next`에 **다음에 할 일**을 적습니다. 다음 턴은 다른 호스트일 수 있습니다.
