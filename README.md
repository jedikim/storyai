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

manuscript/ bible/ store/     원고·설정집·그래프 저장소
server/ web/                 MCP·REST 코어 + React 검수 UI
```

## 문서


`docs/index.html`을 브라우저로 여세요. 일곱 개 문서가 서로를 참조합니다.

| 문서 | 내용 | 대상 |
|---|---|---|
| [index](docs/index.html) | 전체 지도, 핵심 결정 8개, 현황 | 모두 |
| [01 기획서](docs/01-기획서.html) | 문제·근거 숫자·시장 빈틈·사용 시나리오 | 기획자 |
| [02 설계서](docs/02-설계서.html) | 데이터 모델·그래프 툴 15개·프로젝트 제어·전파 | 개발자 |
| [03 개발계획서](docs/03-개발계획서.html) | P0~P6 로드맵·태스크 분해·리스크 | 개발자 |
| [04 기술 스택](docs/04-기술스택.html) | 선택과 거부의 근거·의존성·참고문헌 | 개발자 |
| [05 구조도](docs/05-구조도.html) | 다이어그램 11장 | 모두 |
| [06 UI 설계](docs/06-UI설계.html) | 화면 명세·컴포넌트·디자인 토큰 | 디자이너·개발자 |
| [07 UI 목업](docs/07-UI목업.html) | **실제로 동작하는 화면** | 모두 |

## 기계 판독용 스펙

문서가 설명하는 대상이자 구현이 직접 읽는 파일입니다.

```
spec/ontology.json   노드 18종 · 간선 28종 · 태그 · 삼중 시간축 · 가시성 · 불변식
spec/tools.json      MCP 툴 16개 시그니처 · 예산 · 위험 등급 정책
spec/rules.json      진단 규칙 26개 (Tier 1 결정론 / Tier 2 LLM) · 전파 정책
spec/policy.json     P1 변경 위험 등급 · 잠금 · 캐스케이드 임계값
spec/schema.sql      DDL — 테이블 27 · 뷰 5 · 인덱스 23
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

## 구현 현황

**P0 읽기 전용 인덱스**, **P1 쓰기 경로**, **P2 복선·진단**, **P3 추출·검색**, **P4 UI**,
**P5 Domino v1**, **P6 Domino v2·다중 에이전트 운영**이 구현되어 있습니다.
MCP 도구 16개(그래프 15개 + 프로젝트 제어 1개)를 제공하며, 모든 그래프 쓰기는 제안 기록과 `read_set` 충돌
판정을 거쳐 단일 SQLite 커밋 레인에서 원자적으로 적용됩니다. P3는 명시적 ID binding
manifest와 UTF-8 byte span을 강제하고, BM25와 로컬 sqlite-vec 결과를 RRF로 결합합니다.
P4는 같은 코어를 감싼 FastAPI REST 계층과 React Flow 그래프, F–T–P 복선 보드,
이중 시간축, 출처를 구분하는 inline diff 검수 큐를 제공합니다.
P5는 역방향 `read_set` 의존성, typed projection 조기 종료, 깊이·노드 예산과 순환 차단을
적용하고, 결정론적 재도출 결과도 자동 반영하지 않고 `cascade` Proposal로 남깁니다.
P6는 TTL advisory lease, 부모가 있는 에이전트 session branch, 그리고 commit 밖의 durable
worker에서 실행되는 Tier-2 재도출을 추가합니다. P0~P6 로드맵이 모두 구현되었습니다.

## 여러 소설 프로젝트

한 MCP 프로세스는 여러 소설 프로젝트를 등록하고 현재 프로젝트를 전환할 수 있습니다.
각 프로젝트는 독립적인 `manuscript/`, `bible/`, `spec/`, `store/story.db`를 가지며,
선택 뒤 기존 그래프 도구 15개와 Tier-2 worker는 그 프로젝트에만 작동합니다.

```text
project(mode="current")
project(mode="list")
project(mode="create", name="novel-a", path="/absolute/path/novel-a")
project(mode="register", name="existing", path="/absolute/path/existing")
project(mode="select", name="novel-a")
```

`create`는 현재 프로젝트의 `spec/`을 템플릿으로 복사해 새 프로젝트를 만들고 즉시
선택합니다. `register`도 완전한 기존 프로젝트를 검증한 뒤 선택합니다. 선택은 기본적으로
시작 프로젝트의 `.storyai/projects.json`에 원자적으로 저장되어 MCP 재시작 후 복원됩니다.
테스트나 별도 운영 레지스트리는 `STORYAI_PROJECTS_FILE`로 지정할 수 있습니다. 프로젝트
전환은 진행 중인 다른 MCP 호출이 없을 때 수행하세요.

## 개발 실행

Python 3.12 이상에서 격리 환경을 만들고 개발 의존성을 설치합니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

설정집 파일 형식은 [`bible/README.md`](bible/README.md)를 따릅니다. 로더는 명시된
YAML 메타데이터만 읽으며 내용을 추측하거나 자동 추출하지 않습니다.

```bash
.venv/bin/python -m server.load_bible
.venv/bin/python -m server.consolidate
.venv/bin/storyai-cascade --limit 100
.venv/bin/python -m pytest
server/run-mcp.sh
```

UI는 정적 번들을 빌드한 뒤 같은 Python 프로세스에서 실행합니다.

```bash
cd web && npm ci && npm run build && cd ..
.venv/bin/storyai-ui
```

기본 주소는 `http://127.0.0.1:8765`입니다. 개발 중에는 별도 터미널에서
`cd web && npm run dev`를 실행하면 `/api` 요청이 8765 포트로 프록시됩니다.

원고 추출은 `manuscript/**/*.md` 옆의 `*.story.json`을 입력으로 사용합니다. 형식과
LLM 추출 계약은 [`prompts/v1/`](prompts/v1/)에 있으며, `ingest`는 Proposal만 만들고
자동 커밋하지 않습니다.

마지막 명령은 stdio MCP 서버이므로 터미널에 대기하는 것이 정상입니다. Claude Code와
Codex는 각자의 프로젝트 설정을 통해 동일한 `server/run-mcp.sh` 엔트리포인트를
실행합니다.

## P1 쓰기 계약

`propose`는 live 그래프를 바꾸지 않습니다. 각 op에는 8자 이상의 고유 `idem_key`가
필요하고 `read_set`은 비어 있을 수 없습니다. 최초 Session처럼 기존 노드 근거가 없는
ADD는 현재 `graph_state.revision`을 `{ "node": "book", "rev": n }`으로 전달합니다.

지원 verb는 `ADD · UPDATE · INVALIDATE · LINK · UNLINK`입니다. `INVALIDATE`와
`UNLINK`는 행을 삭제하지 않고 `tx_to`를 닫습니다. `commit(mode="dry_run")`은 실제와
같은 검증·CID·Merkle 계산을 수행한 뒤 전체 트랜잭션을 되돌립니다.

`story://session/latest`는 가장 최근 Session 노드를 가리킵니다. Session의 `props`에는
`open_threads`와 `next`를 반드시 남겨 다음 호스트가 이어받을 수 있게 합니다.

## P2 복선·연속성 계약

Promise의 `props.status`는 `hypothetical → eligible → actualized` 순서로만 진행하며
의도적 폐기는 `prevented`로 끝납니다. `eligible`에는 T, `actualized`에는 P가 필요합니다.
`promises(status=["eligible"])`는 F–T–P와 부채, S-Eff, delta-Coh 근사치를 반환합니다.

Fact의 가시성은 `visible_to`에 viewer, learned_at, pathway를 구조화해 기록합니다. Scene의
`props.pre · post · forbid` 조건은 subject, field, op, value 형태이며, 발화 사실은
`props.claims=[{"speaker": "character/…", "fact": "fact/…"}]`로 기록합니다. 이 구조를
사용해야 `check`가 인지 시점·세계 규칙·사실 충돌을 SQL로 재현 가능하게 판정합니다.

`causes`, `contains`, `extends`의 순환과 Scene당 두 번째 `focalizes` 간선은 제안 단계에서
거부됩니다. 도달 불가 사건을 포함한 나머지 진단은 commit 결과와 `check`에서 확인합니다.

## P5 Domino 계약

accepted Proposal의 `read_set`은 `proposal_read`에 역색인됩니다. 읽었던 노드의 typed
projection이 바뀌면 그 결과를 쓴 노드가 dirty가 되며, 최대 깊이 3·노드 40 예산 안에서
위상 순서로 전파됩니다. `title`, `summary`, `body` 같은 산문만 바뀐 경우에는 전파하지
않습니다. 순환은 기본 거부하고, 명시적으로 `commit(allow_cycles=true,
max_iterations=1..3)`을 호출한 경우에만 bounded mode를 사용합니다.

LLM 없는 재도출은 대상 노드 `props._derive`에 명시한 typed copy만 수행합니다.

```json
{
  "_derive": [{
    "source": "fact/source",
    "source_field": "props.object",
    "target_field": "props.object",
    "transform": "copy"
  }]
}
```

결과는 live 그래프에 바로 적용되지 않습니다. commit 응답의 `cascade.proposals`에
`actor_kind="cascade"`인 새 Proposal이 들어가며, 이를 별도로 검수하고 commit해야 합니다.

## P6 운영 계약

동시 작업 전에는 `lease(mode="acquire", scope="scene/A2.C14.*", ttl_sec=900,
session_id="session/…")`로 권고 리스를 얻습니다. 리스는 겹치는 exact/wildcard scope를
충돌로 알리고 만료 항목을 자동 정리하지만, 그래프 쓰기를 강제로 막지는 않습니다.
`propose(parent_session_id="session/parent")`는 새 session branch의 부모와 시작 graph
revision을 기록합니다. commit된 revision은 해당 branch head로 이동하고 충돌 거부된
branch는 `conflicted`로 표시됩니다.

Tier-2가 필요한 대상은 human-origin 노드의 `props._rederive`에 계약을 둡니다.

```json
{
  "_rederive": [{
    "sources": ["fact/source"],
    "target_field": "summary",
    "instruction": "바뀐 구조 사실에 맞춰 요약을 다시 작성한다.",
    "max_tokens": 1200
  }]
}
```

commit은 `cascade_job`만 원자적으로 적재합니다. worker는 최신 human snapshot 전문과
바뀐 source의 typed projection만 provider에 보내며, 직전 cascade 결과는 입력하지
않습니다. provider는 아래 JSON webhook 계약으로 연결합니다.

```bash
export STORYAI_REDERIVE_ENDPOINT=https://llm-gateway.example/rederive
export STORYAI_REDERIVE_API_KEY=...
.venv/bin/storyai-cascade --limit 100
```

요청에는 `original_human_node`, `changed_sources`, `instruction`, `target_field`,
`max_tokens`가 들어가고 응답은 `{ "value": ..., "model_id": "provider/model" }`입니다.
모델 ID는 Proposal provenance에 보존됩니다. worker 결과 역시 새
`actor_kind="cascade"` Proposal일 뿐이며 자동 commit되지 않습니다. endpoint는 HTTPS만
허용하고, 로컬 테스트에 한해 loopback HTTP를 허용합니다.
