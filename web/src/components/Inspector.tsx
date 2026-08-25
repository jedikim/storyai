import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api";
import { formatDate, formatValue, KIND_LABELS, ORIGIN_LABELS } from "../format";
import type { NodeDetail } from "../types";

interface Props {
  nodeId: string | null;
  asOf: number;
  maxChapter: number;
  onSelect: (id: string) => void;
  onClose: () => void;
  onUpdated: (node: NodeDetail) => void;
}

function TimeTrack({ label, value, max, tone }: { label: string; value: number | null; max: number; tone: string }) {
  const position = value === null ? 0 : Math.max(0, Math.min(100, (value / Math.max(max, 1)) * 100));
  return (
    <div className="time-track">
      <div><span>{label}</span><b>{value === null ? "—" : `${value}장`}</b></div>
      <div className="time-track__bar"><i className={tone} style={{ left: `${position}%` }} /></div>
    </div>
  );
}

export function Inspector({ nodeId, asOf, maxChapter, onSelect, onClose, onUpdated }: Props) {
  const [node, setNode] = useState<NodeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draftSummary, setDraftSummary] = useState("");
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!nodeId) {
      setNode(null);
      return;
    }
    let cancelled = false;
    setError(null);
    setEditing(false);
    setEditError(null);
    setSaved(false);
    void api
      .node(nodeId, asOf)
      .then((value) => {
        if (cancelled) return;
        setNode(value);
        setDraftSummary(value.summary ?? "");
      })
      .catch((reason: Error) => !cancelled && setError(reason.message));
    return () => {
      cancelled = true;
    };
  }, [asOf, nodeId]);

  if (!nodeId) {
    return (
      <aside className="inspector inspector-empty">
        <div className="empty-mark">⌁</div>
        <h2>노드를 선택하세요</h2>
        <p>필드, 시간축, 원문 근거, 참조와 편집 이력이 여기에 표시됩니다.</p>
      </aside>
    );
  }
  if (error) return <aside className="inspector"><div className="error-panel">{error}</div></aside>;
  if (!node) return <aside className="inspector"><div className="loading-panel">노드 읽는 중…</div></aside>;

  const props = node.props ?? {};
  const contract = ["pre", "post", "forbid"].filter((key) => key in props);

  async function saveSummary(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!node) return;
    const summary = draftSummary.trim();
    if (!summary || summary === (node.summary ?? "")) return;
    setSaving(true);
    setEditError(null);
    setSaved(false);
    try {
      const result = await api.updateSummary(node.id, node.rev, summary);
      setNode(result.node);
      setDraftSummary(result.node.summary ?? "");
      setEditing(false);
      setSaved(true);
      onUpdated(result.node);
    } catch (reason) {
      setEditError((reason as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <aside className={`inspector layer-${node.layer}`} aria-label={`${node.title} 인스펙터`}>
      <header className="inspector__header">
        <button className="inspector-close" aria-label="상세 닫기" onClick={onClose}>×</button>
        <div className="eyebrow">{node.kind} · {KIND_LABELS[node.kind] ?? node.kind}</div>
        <h2>{node.title}</h2>
        <code>story://{node.id}</code>
        <div className="inspector-badges">
          <span className={`origin-badge ${node.origin}`}>{ORIGIN_LABELS[node.origin]}</span>
          <span className="neutral-badge">r{node.rev}</span>
          {node.locked && <span className="locked-badge">◆ canon 잠금</span>}
        </div>
      </header>
      <section className="summary-section">
        <div className="section-heading">
          <h3>설명</h3>
          {!node.locked && !editing && (
            <button className="button" onClick={() => { setEditing(true); setSaved(false); }}>
              편집
            </button>
          )}
        </div>
        {editing ? (
          <form className="summary-editor" onSubmit={(event) => void saveSummary(event)}>
            <textarea
              aria-label="노드 설명"
              value={draftSummary}
              disabled={saving}
              maxLength={8000}
              rows={8}
              onChange={(event) => setDraftSummary(event.target.value)}
            />
            {editError && <p className="editor-message error" role="alert">{editError}</p>}
            <div className="editor-actions">
              <button
                className="button"
                type="button"
                disabled={saving}
                onClick={() => { setDraftSummary(node.summary ?? ""); setEditing(false); setEditError(null); }}
              >
                취소
              </button>
              <button
                className="button primary"
                type="submit"
                disabled={saving || !draftSummary.trim() || draftSummary.trim() === (node.summary ?? "")}
              >
                {saving ? "저장 중…" : "저장"}
              </button>
            </div>
          </form>
        ) : (
          <p className="summary-copy">{node.summary || "설명이 없습니다."}</p>
        )}
        {node.locked && <p className="editor-message">◆ canon 잠금 노드는 편집할 수 없습니다.</p>}
        {saved && <p className="editor-message success" role="status">저장했습니다.</p>}
      </section>
      <section>
        <h3>필드</h3>
        <dl className="field-grid">
          {Object.entries(props).map(([key, value]) => (
            <div key={key}><dt>{key}</dt><dd>{formatValue(value)}</dd></div>
          ))}
          {!Object.keys(props).length && <div><dt>속성</dt><dd>—</dd></div>}
        </dl>
      </section>
      <section>
        <h3>삼중 시간축</h3>
        <TimeTrack label="story · 작중" value={node.story_from} max={maxChapter} tone="story" />
        <TimeTrack label="discourse · 독자 인지" value={node.reveal_at} max={maxChapter} tone="reveal" />
        <div className="transaction-track"><span>transaction · 편집</span><b>r{node.rev} · 활성</b></div>
        {node.story_from !== null && node.reveal_at !== null && node.story_from < node.reveal_at && (
          <p className="flashback-note">↺ 작중 시간보다 뒤늦게 공개된 회상 정보입니다.</p>
        )}
      </section>
      {contract.length > 0 && (
        <section>
          <h3>전이 계약</h3>
          <dl className="contract-grid">
            {contract.map((key) => <div key={key}><dt>{key}</dt><dd>{formatValue(props[key])}</dd></div>)}
          </dl>
        </section>
      )}
      <section>
        <h3>원문 근거</h3>
        {node.evidence.length ? node.evidence.map((evidence, index) => (
          <figure className="evidence" key={`${evidence.file}-${evidence.start}-${index}`}>
            <blockquote>{evidence.quote || node.body || "근거 인용 없음"}</blockquote>
            <figcaption>{evidence.file} · {evidence.start ?? "?"}–{evidence.end ?? "?"} bytes</figcaption>
          </figure>
        )) : <p className="muted-copy">연결된 원문 근거가 없습니다.</p>}
      </section>
      <section>
        <h3>참조 <span>{node.refs.length}</span></h3>
        <div className="ref-list">
          {node.refs.map((ref, index) => (
            <button
              key={`${ref.id}-${ref.rel}-${index}`}
              className={`ref-row${ref.hard ? "" : " soft"}`}
              onClick={() => onSelect(ref.id)}
            >
              <span>{ref.rel}</span><b>{ref.title}</b><small>{ref.direction === "out" ? "→" : "←"}</small>
            </button>
          ))}
          {!node.refs.length && <p className="muted-copy">연결된 참조가 없습니다.</p>}
        </div>
      </section>
      <section>
        <h3>편집 이력</h3>
        <div className="history-list">
          {node.history.map((item) => (
            <article key={item.rev}>
              <span className={`origin-badge ${item.origin}`}>{ORIGIN_LABELS[item.origin]}</span>
              <div><b>r{item.rev} · {item.fields.map((field) => field.field).join(", ") || "초기 상태"}</b>
                <p>{item.rationale || item.model_id || "초기 인덱스"}</p>
                <time>{formatDate(item.tx_from)}</time>
              </div>
            </article>
          ))}
        </div>
      </section>
    </aside>
  );
}
