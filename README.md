# storyai

> ## 먼저 이걸 여세요 → `docs/index.html`
> 브라우저로 열면 문서 일곱 개가 서로 링크로 이어집니다.
> 한 장으로 다 보고 싶으면 `docs/storyai-설계-통합.html`.
> 화면이 궁금하면 `docs/07-UI목업.html` — 실제로 동작합니다.

## 이 폴더에 뭐가 있나

```
docs/     읽을 것.  설계 문서 7종 + 통합본
spec/     구현이 읽을 것.  온톨로지·툴·규칙·DDL (JSON + SQL)
build/    문서를 고칠 때.  parts/ 수정 후 build.py 실행
skills/   에이전트가 읽을 것.  집필·검수·설정집 3종
hooks/    자동 검사 트리거.  Claude Code / Codex 각각
AGENTS.md 에이전트 지침 단일 소스 ★ 양쪽 호스트가 읽음

manuscript/ api/ web/        ← 후속 단계에서 채웁니다
server/ bible/ store/        ← P0 읽기 전용 인덱스 구현
```

## 문서


`docs/index.html`을 브라우저로 여세요. 일곱 개 문서가 서로를 참조합니다.

| 문서 | 내용 | 대상 |
|---|---|---|
| [index](docs/index.html) | 전체 지도, 핵심 결정 8개, 현황 | 모두 |
| [01 기획서](docs/01-기획서.html) | 문제·근거 숫자·시장 빈틈·사용 시나리오 | 기획자 |
| [02 설계서](docs/02-설계서.html) | 데이터 모델·MCP 툴 15개·버전관리·전파 | 개발자 |
| [03 개발계획서](docs/03-개발계획서.html) | P0~P6 로드맵·태스크 분해·리스크 | 개발자 |
| [04 기술 스택](docs/04-기술스택.html) | 선택과 거부의 근거·의존성·참고문헌 | 개발자 |
| [05 구조도](docs/05-구조도.html) | 다이어그램 11장 | 모두 |
| [06 UI 설계](docs/06-UI설계.html) | 화면 명세·컴포넌트·디자인 토큰 | 디자이너·개발자 |
| [07 UI 목업](docs/07-UI목업.html) | **실제로 동작하는 화면** | 모두 |

## 기계 판독용 스펙

문서가 설명하는 대상이자 구현이 직접 읽는 파일입니다.

```
spec/ontology.json   노드 16종 · 간선 24종 · 태그 · 삼중 시간축 · 가시성 · 불변식
spec/tools.json      MCP 툴 15개 시그니처 · 예산 · 위험 등급 정책
spec/rules.json      진단 규칙 26개 (Tier 1 결정론 / Tier 2 LLM) · 전파 정책
spec/schema.sql      DDL — 테이블 15 · 뷰 5 · 인덱스 29
```

`sqlite3 store/story.db < spec/schema.sql` 로 바로 적용됩니다.

## 호스트 패키지

Claude Code와 Codex 양쪽에서 **한 벌로** 동작합니다.

```
plugin.json                 agent-plugins 스키마 — Codex가 우선 인식
.claude-plugin/plugin.json  Claude Code 매니페스트 — Codex도 폴백으로 읽음
.mcp.json                   Claude Code용 프로젝트 MCP 설정
.codex/config.toml          Codex용 프로젝트 MCP 설정
AGENTS.md                   ★ 에이전트 지침 단일 소스. 양쪽이 읽음
CLAUDE.md                   AGENTS.md를 가리키는 한 줄
skills/                     Agent Skills 규격 6필드만 — 양쪽 + 46개 호스트
hooks/                      run-check.sh 를 양쪽 훅이 공통 호출
```

### MCP 연결 확인

저장소에는 Claude Code용 `.mcp.json`과 Codex용 `.codex/config.toml`이 모두 들어 있습니다.
처음 열 때 프로젝트를 신뢰하고 MCP 실행을 승인한 다음 연결 상태를 확인합니다.

```bash
claude mcp list
codex mcp list
```

## 문서 수정

문서는 빌드 산출물입니다. 내용을 고치려면 `build/parts/*.part.html`을 고치고:

```bash
python3 build/build.py
```

공통 스타일은 `build/style.css` 한 곳에 있고, 빌드 시 각 문서에 인라인됩니다 —
그래서 문서 하나하나가 자립형이고 어디에 올려도 그대로 렌더됩니다.
의존성은 파이썬 표준 라이브러리뿐입니다.

## 다음 단계

**P0 — 읽기 전용 인덱스, 1~2주.** 스키마를 SQLite에 올리고 툴 다섯 개
(`graph_schema · outline · find · get · refs`)만 만듭니다.
완료 기준은 하나 — 기존 원고를 넣고 Claude Code에서
**"한도영이 나오는 씬 전부"**가 답해지는 것.

자세한 분해는 [개발계획서 §P0](docs/03-개발계획서.html#p0).

## P0 개발 실행

Python 3.12 이상에서 격리 환경을 만들고 개발 의존성을 설치합니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

설정집 파일 형식은 [`bible/README.md`](bible/README.md)를 따릅니다. 로더는 명시된
YAML 메타데이터만 읽으며 내용을 추측하거나 자동 추출하지 않습니다.

```bash
.venv/bin/python -m server.load_bible
.venv/bin/python -m pytest
server/run-mcp.sh
```

마지막 명령은 stdio MCP 서버이므로 터미널에 대기하는 것이 정상입니다. Claude Code와
Codex는 각자의 프로젝트 설정을 통해 동일한 `server/run-mcp.sh` 엔트리포인트를
실행합니다.
