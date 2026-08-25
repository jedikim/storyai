import { useEffect, useState } from "react";

import { api } from "../api";
import { formatDate, formatValue, KIND_LABELS, ORIGIN_LABELS } from "../format";
import type { NodeDetail } from "../types";

interface Props {
  nodeId: string | null;
  asOf: number;
  maxChapter: number;
  onSelect: (id: string) => void;
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

export function Inspector({ nodeId, asOf, maxChapter, onSelect }: Props) {
  const [node, setNode] = useState<NodeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!nodeId) {
      setNode(null);
      return;
    }
    let cancelled = false;
    setError(null);
    void api
      .node(nodeId, asOf)
      .then((value) => !cancelled && setNode(value))
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
  return (
    <aside className={`inspector layer-${node.layer}`} aria-label={`${node.title} 인스펙터`}>
      <header className="inspector__header">
        <div className="eyebrow">{node.kind} · {KIND_LABELS[node.kind] ?? node.kind}</div>
        <h2>{node.title}</h2>
        <code>story://{node.id}</code>
        <div className="inspector-badges">
          <span className={`origin-badge ${node.origin}`}>{ORIGIN_LABELS[node.origin]}</span>
          <span className="neutral-badge">r{node.rev}</span>
          {node.locked && <span className="locked-badge">◆ canon 잠금</span>}
        </div>
      </header>
      <section>
        <h3>필드</h3>
        <dl className="field-grid">
          {Object.entries(props).map(([key, value]) => (
            <div key={key}><dt>{key}</dt><dd>{formatValue(value)}</dd></div>
          ))}
          {!Object.keys(props).length && <div><dt>summary</dt><dd>{node.summary || "—"}</dd></div>}
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
