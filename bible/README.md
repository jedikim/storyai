# Bible bootstrap format

P0의 설정집 로더는 추측하거나 자동 추출하지 않습니다. 각 Markdown 파일의 YAML front matter에
그래프 필드를 명시하고, 그 아래 본문은 해당 노드의 근거 원문으로 색인합니다.

표준 하위 폴더는 `characters/`, `locations/`, `objects/`, `scenes/`, `rules/`,
`promises/`이며 폴더명으로 `kind`를 생략할 수 있습니다.

```markdown
---
id: character/한도영
title: 한도영
summary: 등대지기. 3장부터 용의선상.
aliases: [도영, 등대지기]
tags: [POV, Suspect]
props:
  역할: 등대지기
  주손: 왼손
edges:
  - rel: performs
    to: scene/A1.C03.S01
---

# 한도영

인물 설정 본문을 씁니다.
```

로더는 P0의 공개 타입 여섯 개만 허용하고, 간선의 `src`/`dst` 제약을
`spec/ontology.json`으로 검증합니다. 대상은 반드시 절대 주소로 적습니다.

```bash
python3 -m server.load_bible
```
