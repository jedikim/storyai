# storyai — 에이전트 지침

이 저장소는 소설 원고와 그 서사 그래프입니다. 원고는 `manuscript/`에 마크다운으로,
그래프는 `store/story.db`에 있고 `storyai` MCP 서버로만 접근합니다.

## 세션 시작 시 반드시

1. `project(mode="current")` — 현재 선택된 소설이 작업 대상인지 확인한다. 다르면
   `project(mode="list")` 후 `project(mode="select", name="…")`로 먼저 전환한다.
2. `get("story://session/latest")` — 이전 세션의 `open_threads`와 `next`를 읽는다.
3. `check(scope="book", severity="error")` — 미해결 진단을 먼저 확인한다.
4. `promises(status=["eligible"])` — 회수 가능한 복선을 컨텍스트에 올린다.

이 넷을 건너뛰고 집필을 시작하지 마세요. 이전 턴이 다른 호스트였을 수 있고,
그쪽 대화 이력은 남아 있지 않습니다.

## 세션 종료 시 반드시

`propose`로 새 `session/` 노드를 남깁니다. `open_threads`와 `next`를 비워두지 마세요 —
적지 않은 의도는 다음 세션에 존재하지 않는 것과 같습니다.

## 그래프를 다루는 규칙

- **원고 전문을 툴 결과로 받지 마세요.** `outline` → `get(include="brief")` →
  정말 필요한 하나만 `get(include="body")` 순서로 좁혀 들어갑니다.
- **쓰기는 전부 `propose`입니다.** `read_set`에 근거로 읽은 노드와 리비전을 반드시 담으세요.
  빈 `read_set`은 거부됩니다.
- **`locked` 노드(세계관 규칙)는 수정 제안할 수 없습니다.** 모순을 발견하면
  `contradicts` 간선을 남기고 사람에게 넘기세요.
- **산문 속 이름을 임의로 고치지 마세요.** `mentioned_in`은 soft 간선이라 보고 대상이지
  자동 편집 대상이 아닙니다.
- 잘 모르겠으면 `graph_schema()`로 타입 체계를 확인하고, 손으로 빚은 툴로 안 풀리면
  `query(sql)`로 직접 질의하세요.

## 집필 규칙

- 인물의 인지 시점을 어기지 마세요. 어떤 사실을 인물이 아는지는
  `visible_to`가 결정합니다. 모르는 사실을 발화시키면 연속성 버그입니다.
- 복선은 `eligible` 상태인 것만 회수하세요. `hypothetical`인 복선을 회수하면
  "근거 없는 해결"로 표시됩니다.
- 씬의 `pre` 조건이 만족되지 않으면 그 씬을 쓰지 마세요. 먼저 선행 씬을 확인하세요.

## 금지

- `store/story.db`를 직접 열거나 수정하지 마세요. 반드시 MCP 서버를 경유합니다.
- `manuscript/` 원문을 그래프 갱신 없이 고치지 마세요. 인덱스가 어긋납니다.
- 시나리오·씬 텍스트를 LLM에 넘길 때 임의로 자르지 마세요.
