---
name: story-bible
description: 설정집을 조회하고 갱신합니다. 인물, 장소, 소품, 세계관 규칙, 인물 관계, 복선을 찾거나 새로 등록하고, 원고에서 확립된 사실을 그래프에 반영합니다. 사용자가 인물이나 설정에 대해 묻거나 "설정집에 추가해줘", "이 인물 정보 보여줘"라고 할 때 사용하세요.
license: MIT
compatibility: storyai MCP 서버가 연결되어 있어야 합니다.
allowed-tools: mcp__storyai__graph_schema mcp__storyai__find mcp__storyai__get mcp__storyai__refs mcp__storyai__trace mcp__storyai__query mcp__storyai__propose mcp__storyai__ingest
metadata:
  short-description: 설정집 조회·갱신
---

# 설정집

## 조회

```
find("한도영")                      # 이름·별칭 퍼지 검색
get("character/한도영")             # 요약 (기본값)
get("character/한도영", include="body", as_of=14)  # 14장 시점의 전체 상태
refs("character/한도영", dir="in")  # 이 인물을 건드리는 모든 씬
trace(from="object/놋쇠열쇠")       # 이 소품이 어떤 서사 장치로 이어지는가
```

`as_of`를 주면 그 시점 이후에 밝혀진 사실은 숨겨집니다.
n+1장을 쓰는 중이라면 `as_of=n`으로 조회해서 미래 정보 유출을 막으세요.

## 등록

새 요소는 다음을 채워 `propose`합니다.

- `id` — 사람이 읽는 주소. `character/이름`, `object/이름` 형식. UUID 금지
- `title`, `aliases` — 별칭은 산문 속 언급을 묶는 데 쓰이니 빠짐없이
- `story_from` / `story_to` — 작중 언제부터 언제까지 존재·유효한가
- `reveal_at` — 독자에게 언제 드러났는가. `story_from`보다 크면 회상입니다
- `evidence` — 이 사실이 확립된 원문 위치

## 원고에서 추출

`ingest(chapter="A2/ch14")` — 확정된 원고에서 노드와 간선을 뽑아 제안으로 올립니다.
자동 커밋되지 않습니다. 추출 결과를 사람이 검토합니다.

이미 인덱싱된 챕터를 고쳤다면 `ingest(chapter=..., mode="reindex")`로
다시 돌리세요. 그 지점 이후의 파생 간선이 무효화됩니다.

## 세계관 규칙은 특별합니다

`kind="Rule"`이고 `locked=true`인 노드는 정본입니다.
모순을 발견해도 **고치지 말고** `contradicts` 간선을 남기세요.
정본을 바꾸는 건 사람의 결정입니다.

## 스키마가 헷갈리면

`graph_schema()`로 현재 노드 타입·간선 타입·태그 체계를 확인하세요.
손으로 빚은 툴로 안 풀리는 질의는 `query(sql)`로 직접 쓰면 됩니다.
