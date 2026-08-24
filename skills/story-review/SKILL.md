---
name: story-review
description: 원고의 연속성을 검수합니다. 시간 모순, 인물의 지식 경계 위반, 세계관 규칙 위반, 미회수 복선, 근거 없이 터진 회수를 찾아 진단으로 기록합니다. 고치지는 않습니다. 사용자가 "검수해줘", "연속성 확인", "모순 찾아줘"라고 할 때 사용하세요.
license: MIT
compatibility: storyai MCP 서버가 연결되어 있어야 합니다. 집필에 쓴 것과 다른 모델로 실행하는 것을 권장합니다.
allowed-tools: mcp__storyai__check mcp__storyai__promises mcp__storyai__refs mcp__storyai__trace mcp__storyai__impact mcp__storyai__get mcp__storyai__query
metadata:
  short-description: 연속성 검수 · 진단만
---

# 연속성 검수

## 중요 — 고치지 마세요

이 스킬은 **읽기 전용**입니다. 발견한 문제를 진단으로 기록하고 사람에게 보고할 뿐,
원고나 그래프를 수정하지 않습니다. 고치면서 새 모순을 만드는 것이 가장 흔한 실패입니다.
수정은 다음 집필 턴이나 사람이 합니다.

## 1. 결정론 진단부터

```
check(scope="book", severity="error")
check(scope="book", severity="warn")
```

이 규칙들은 LLM 없이 그래프 연산으로 돌아갑니다. 여기서 나온 건 **확정된 문제**입니다.

## 2. 복선 부채 확인

```
promises(status=["hypothetical"], sort="debt")   # 방치 위험
promises(status=["eligible"], sort="age")        # 오래 대기 중
```

원고가 끝나가는데 `hypothetical`이 남아 있으면 방치된 복선입니다.
트리거가 정의되지 않은 복선은 회수 가능 여부 자체를 판정할 수 없으니 별도로 보고하세요.

## 3. 검증 예산은 중반부와 고엔트로피 구간에

장편의 일관성 오류는 **서사 위치 40~60% 구간**에 몰리고,
문장 엔트로피가 높은 구간에 몰립니다. 전 구간을 균등하게 훑지 말고
그쪽에 시간을 쓰세요.

## 4. 결정론이 못 잡는 것

의미적 모순은 규칙으로 안 잡힙니다. 의심되는 지점은
`trace`로 인과 경로를 따라가고 `refs`로 그 요소를 건드리는 씬을 전부 본 뒤 판단하세요.

## 5. 보고

각 진단마다 이렇게 남깁니다.

- 무엇이 어긋났는가 (한 문장)
- 근거 — 원문 인용과 위치 두 곳 이상
- 어느 쪽이 틀렸다고 보는가, 그리고 왜
- 고치면 무엇이 함께 바뀌는가 (`impact`로 확인)

`contradicts` 간선을 남기는 것까지가 이 스킬의 쓰기 권한입니다.
그 이상은 하지 마세요.
