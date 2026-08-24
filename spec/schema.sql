-- storyai 서사 그래프 스키마
-- 문서: docs/02-설계서.html#sql
-- 적용:  sqlite3 store/story.db < spec/schema.sql
--
-- 설계 원칙
--   1. 삭제하지 않는다. INVALIDATE는 tx_to를 닫을 뿐.
--   2. 삼중 시간축 — story(작중) / discourse(독자 인지) / transaction(편집)
--   3. 역방향 간선 인덱스가 핵심. "이걸 가리키는 게 뭐냐"가 가장 많이 쓰인다.
--   4. 원고 원문은 파일에 있고 여기엔 좌표만.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ═══════════════════════════════════════════════════════════
-- 노드
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS node (
  id            TEXT PRIMARY KEY,             -- "character/한도영" · UUID 금지
  kind          TEXT NOT NULL,                -- spec/ontology.json 의 kinds
  title         TEXT NOT NULL,
  summary       TEXT,                         -- hover 등가물, 한 줄
  props         TEXT NOT NULL DEFAULT '{}',   -- JSON, 타입별 필드

  -- 삼중 시간축 ─ P1에 반드시 들어가야 함. 나중에 얹을 수 없음
  story_from    INTEGER,                      -- 작중 유효 시작 (장)
  story_to      INTEGER,                      -- NULL = 열림
  reveal_at     INTEGER,                      -- 독자 인지 시점 (장)
  tx_from       TEXT NOT NULL,                -- ISO8601
  tx_to         TEXT,                         -- NULL = 현재 유효

  origin        TEXT NOT NULL,                -- human | agent | cascade
  locked        INTEGER NOT NULL DEFAULT 0,   -- canon 잠금
  rev           INTEGER NOT NULL DEFAULT 1,   -- 낙관적 동시성
  cid           TEXT NOT NULL,                -- 정규 직렬화 해시

  CHECK (story_to IS NULL OR story_from IS NULL OR story_to >= story_from),
  CHECK (origin IN ('human','agent','cascade'))
);
CREATE INDEX IF NOT EXISTS ix_node_kind   ON node(kind, tx_to);
CREATE INDEX IF NOT EXISTS ix_node_story  ON node(story_from, story_to);
CREATE INDEX IF NOT EXISTS ix_node_reveal ON node(reveal_at);
CREATE INDEX IF NOT EXISTS ix_node_live   ON node(tx_to) WHERE tx_to IS NULL;

CREATE TABLE IF NOT EXISTS node_alias (
  node          TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  alias         TEXT NOT NULL,
  PRIMARY KEY (node, alias)
);
CREATE INDEX IF NOT EXISTS ix_alias ON node_alias(alias);

-- ═══════════════════════════════════════════════════════════
-- 간선 ─ 노드와 동일한 시간축
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS edge (
  id            INTEGER PRIMARY KEY,
  src           TEXT NOT NULL REFERENCES node(id),
  dst           TEXT NOT NULL REFERENCES node(id),
  rel           TEXT NOT NULL,
  hard          INTEGER NOT NULL DEFAULT 1,   -- 0 = soft(산문 언급) · 자동 편집 금지
  props         TEXT DEFAULT '{}',
  story_from    INTEGER,
  story_to      INTEGER,
  tx_from       TEXT NOT NULL,
  tx_to         TEXT,
  origin        TEXT NOT NULL,
  confidence    REAL DEFAULT 1.0,
  CHECK (origin IN ('human','agent','cascade'))
);
CREATE INDEX IF NOT EXISTS ix_edge_out ON edge(src, rel, tx_to);
CREATE INDEX IF NOT EXISTS ix_edge_in  ON edge(dst, rel, tx_to);   -- ★ 역방향
CREATE INDEX IF NOT EXISTS ix_edge_rel ON edge(rel, tx_to);

-- ═══════════════════════════════════════════════════════════
-- 다중 상속 ─ 태그(클래스) + 피처(프로퍼티 번들)
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS tag (
  name          TEXT PRIMARY KEY,             -- "#POV"
  schema        TEXT DEFAULT '{}'             -- 이 태그가 요구하는 프로퍼티
);
CREATE TABLE IF NOT EXISTS tag_extends (       -- 다중 부모 허용, 순환 금지
  child         TEXT NOT NULL REFERENCES tag(name),
  parent        TEXT NOT NULL REFERENCES tag(name),
  PRIMARY KEY (child, parent)
);
CREATE TABLE IF NOT EXISTS node_tag (
  node          TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  tag           TEXT NOT NULL REFERENCES tag(name),
  PRIMARY KEY (node, tag)
);
CREATE INDEX IF NOT EXISTS ix_node_tag_rev ON node_tag(tag);

CREATE TABLE IF NOT EXISTS feature (           -- 평면 데이터 → 다이아몬드 문제 없음
  node          TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  data          TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (node, name)
);

-- ═══════════════════════════════════════════════════════════
-- 가시성 ─ 누가 무엇을 언제 아는가
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS visibility (
  fact          TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  viewer        TEXT NOT NULL,                -- 'reader' | character/…
  learned_at    INTEGER,                      -- 인지 시점 (장)
  pathway       TEXT,                         -- direct | observed | told | common
  PRIMARY KEY (fact, viewer),
  CHECK (pathway IS NULL OR pathway IN ('direct','observed','told','common'))
);
CREATE INDEX IF NOT EXISTS ix_vis_viewer ON visibility(viewer, learned_at);

-- ═══════════════════════════════════════════════════════════
-- 원문 근거 ─ 원고는 파일에, 여기엔 좌표만
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS evidence (
  id            INTEGER PRIMARY KEY,
  node          TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  file          TEXT NOT NULL,                -- manuscript/A1/ch03.md
  start_off     INTEGER,
  end_off       INTEGER,
  quote         TEXT                          -- 캐시된 짧은 인용
);
CREATE INDEX IF NOT EXISTS ix_evidence_node ON evidence(node);

-- ═══════════════════════════════════════════════════════════
-- 편집 로그 ─ read_set이 충돌·프로버넌스·전파를 동시에 해결
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS proposal (
  id            TEXT PRIMARY KEY,
  actor_kind    TEXT NOT NULL,                -- human | agent | cascade
  model_id      TEXT,
  session_id    TEXT,
  host          TEXT,                         -- claude-code | codex | ui
  rationale     TEXT,
  read_set      TEXT NOT NULL,                -- JSON [{node, rev}] ★ 빈 값 거부
  status        TEXT NOT NULL,                -- open|accepted|rejected|superseded
  ts            TEXT NOT NULL,
  CHECK (actor_kind IN ('human','agent','cascade')),
  CHECK (status IN ('open','accepted','rejected','superseded'))
);
CREATE INDEX IF NOT EXISTS ix_proposal_status ON proposal(status, ts);
CREATE INDEX IF NOT EXISTS ix_proposal_model  ON proposal(model_id);

CREATE TABLE IF NOT EXISTS op (
  id            INTEGER PRIMARY KEY,
  proposal      TEXT NOT NULL REFERENCES proposal(id),
  seq           INTEGER NOT NULL,
  verb          TEXT NOT NULL,                -- ADD|UPDATE|INVALIDATE|LINK|UNLINK
  target        TEXT NOT NULL,
  field         TEXT,
  from_val      TEXT,                         -- from이 있어야 조건부 op가 됨
  to_val        TEXT,
  basis_rev     INTEGER,
  idem_key      TEXT UNIQUE,                  -- MCP 재시도 중복 방지
  ts            TEXT NOT NULL,
  CHECK (verb IN ('ADD','UPDATE','INVALIDATE','LINK','UNLINK'))
);
CREATE INDEX IF NOT EXISTS ix_op_target ON op(target, ts);

-- 프로버넌스 ─ PROV-O 어휘만 빌림
CREATE TABLE IF NOT EXISTS provenance (
  node_version  TEXT NOT NULL,                -- node.id + ':' + rev
  generated_by  TEXT REFERENCES proposal(id), -- prov:wasGeneratedBy
  derived_from  TEXT,                         -- prov:wasDerivedFrom (이전 node_version)
  attributed_to TEXT NOT NULL,                -- prov:wasAttributedTo
  on_behalf_of  TEXT,                         -- prov:actedOnBehalfOf (LLM → 사람)
  ts            TEXT NOT NULL,
  PRIMARY KEY (node_version)
);

-- P1 리비전 스냅샷. node는 빠른 현재 상태, 이 테이블은 덮어쓰기 전 이력이다.
CREATE TABLE IF NOT EXISTS node_revision (
  node          TEXT NOT NULL REFERENCES node(id),
  rev           INTEGER NOT NULL,
  snapshot      TEXT NOT NULL,                -- 정규 JSON 전체 상태
  cid           TEXT NOT NULL,
  tx_from       TEXT NOT NULL,
  tx_to         TEXT,
  proposal      TEXT REFERENCES proposal(id),
  PRIMARY KEY (node, rev)
);
CREATE INDEX IF NOT EXISTS ix_node_revision_cid ON node_revision(cid);

-- 필드 단위 PROV-O 매핑. 같은 노드 리비전 안에서도 변경 근거를 분리한다.
CREATE TABLE IF NOT EXISTS field_provenance (
  node          TEXT NOT NULL REFERENCES node(id),
  rev           INTEGER NOT NULL,
  field         TEXT NOT NULL,
  generated_by  TEXT REFERENCES proposal(id),
  derived_from  TEXT,
  attributed_to TEXT NOT NULL,
  on_behalf_of  TEXT,
  ts            TEXT NOT NULL,
  PRIMARY KEY (node, rev, field)
);

-- 정책 판정과 충돌은 제안과 함께 보존한다. 충돌 제안도 삭제하지 않는다.
CREATE TABLE IF NOT EXISTS proposal_assessment (
  proposal      TEXT PRIMARY KEY REFERENCES proposal(id),
  risk          TEXT NOT NULL,
  reasons       TEXT NOT NULL DEFAULT '[]',
  conflicts     TEXT NOT NULL DEFAULT '[]',
  pending_overlap TEXT NOT NULL DEFAULT '[]',
  CHECK (risk IN ('auto','review','always'))
);

CREATE TABLE IF NOT EXISTS proposal_actor (
  proposal      TEXT PRIMARY KEY REFERENCES proposal(id),
  on_behalf_of  TEXT
);

-- 그래프 Merkle 루트와 단조 증가 book 리비전.
CREATE TABLE IF NOT EXISTS graph_state (
  singleton     INTEGER PRIMARY KEY CHECK (singleton = 1),
  revision      INTEGER NOT NULL,
  root_cid      TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commit_record (
  proposal      TEXT PRIMARY KEY REFERENCES proposal(id),
  graph_revision INTEGER NOT NULL,
  root_cid      TEXT NOT NULL,
  result        TEXT NOT NULL,
  committed_at  TEXT NOT NULL
);

-- ═══════════════════════════════════════════════════════════
-- 진단 ─ check()의 결과가 노드에 붙어 있어야 함
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS diagnostic (
  id            INTEGER PRIMARY KEY,
  rule          TEXT NOT NULL,                -- spec/rules.json 의 id
  severity      TEXT NOT NULL,                -- error | warn | info
  node          TEXT REFERENCES node(id) ON DELETE CASCADE,
  related       TEXT,                         -- JSON [node_id]
  message       TEXT NOT NULL,
  evidence      TEXT,                         -- JSON [{file,start,end,quote}]
  detected_at   TEXT NOT NULL,
  resolved_at   TEXT,                         -- NULL = 미해결
  CHECK (severity IN ('error','warn','info'))
);
CREATE INDEX IF NOT EXISTS ix_diag_open ON diagnostic(severity, resolved_at)
  WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_diag_node ON diagnostic(node);

-- ═══════════════════════════════════════════════════════════
-- 전파 ─ 도미노 v1은 LLM 없이
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS cascade_run (
  id            TEXT PRIMARY KEY,
  trigger_op    INTEGER REFERENCES op(id),
  depth_reached INTEGER,
  nodes_visited INTEGER,
  cutoff_hits   INTEGER,                      -- early cutoff로 멈춘 횟수
  status        TEXT NOT NULL,                -- running|done|budget_exceeded|cycle
  ts            TEXT NOT NULL
);

-- 동시 다중 에이전트에서만 사용. 스위칭 운용에서는 불필요
CREATE TABLE IF NOT EXISTS lease (
  id            TEXT PRIMARY KEY,
  scope         TEXT NOT NULL,                -- "scene/A2.C14.*"
  session_id    TEXT NOT NULL,
  model_id      TEXT,
  note          TEXT,
  acquired_at   TEXT NOT NULL,
  expires_at    TEXT NOT NULL                 -- TTL 필수 — 죽은 세션 자동 해제
);
CREATE INDEX IF NOT EXISTS ix_lease_live ON lease(expires_at);

-- ═══════════════════════════════════════════════════════════
-- 검색
-- ═══════════════════════════════════════════════════════════
CREATE VIRTUAL TABLE IF NOT EXISTS node_fts USING fts5(
  id UNINDEXED, title, aliases, summary, body,
  tokenize = 'unicode61 remove_diacritics 2'
);
-- 벡터는 sqlite-vec 로드 후:
--   CREATE VIRTUAL TABLE node_vec USING vec0(id TEXT PRIMARY KEY, emb float[1024]);

-- ═══════════════════════════════════════════════════════════
-- 자주 쓰는 뷰
-- ═══════════════════════════════════════════════════════════
CREATE VIEW IF NOT EXISTS live_node AS
  SELECT * FROM node WHERE tx_to IS NULL;

CREATE VIEW IF NOT EXISTS live_edge AS
  SELECT * FROM edge WHERE tx_to IS NULL;

-- 미회수 복선 — 이게 SQL 한 줄로 나와야 스키마가 맞는 것
CREATE VIEW IF NOT EXISTS open_promise AS
  SELECT id, title,
         json_extract(props,'$.status') AS status,
         json_extract(props,'$.debt')   AS debt,
         story_from AS planted_at
  FROM live_node
  WHERE kind = 'Promise'
    AND json_extract(props,'$.status') IN ('hypothetical','eligible')
  ORDER BY debt DESC;

-- 회상 — story_from < reveal_at 인 노드. 별도 태그 없이 자동으로 나옴
CREATE VIEW IF NOT EXISTS flashback AS
  SELECT id, kind, title, story_from, reveal_at,
         reveal_at - story_from AS gap
  FROM live_node
  WHERE story_from IS NOT NULL AND reveal_at IS NOT NULL
    AND reveal_at > story_from
  ORDER BY reveal_at;

-- 모델별 일관성 오류 밀도 — 스위칭 운용의 A/B 비교
CREATE VIEW IF NOT EXISTS ced_by_model AS
  SELECT p.model_id,
         COUNT(DISTINCT d.id) * 10000.0 /
           NULLIF(SUM(DISTINCT CAST(json_extract(s.props,'$.word_count') AS REAL)), 0) AS ced,
         COUNT(DISTINCT s.id) AS scenes
  FROM proposal p
    JOIN op   o ON o.proposal = p.id
    JOIN node s ON s.id = o.target AND s.kind = 'Scene'
    LEFT JOIN diagnostic d ON d.node = s.id AND d.resolved_at IS NULL
  WHERE p.status = 'accepted' AND p.model_id IS NOT NULL
  GROUP BY p.model_id;
