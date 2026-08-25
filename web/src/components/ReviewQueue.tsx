import { useState } from "react";

import { api } from "../api";
import { formatDate, formatValue, ORIGIN_LABELS, proposalState } from "../format";
import type { Proposal, ProposalOperation } from "../types";

interface Props {
  proposals: Proposal[];
  onReload: () => Promise<void>;
}

function verbSymbol(operation: ProposalOperation): string {
  if (operation.verb === "ADD" || operation.verb === "LINK") return "+";
  if (operation.verb === "INVALIDATE" || operation.verb === "UNLINK") return "−";
  return "~";
}

export function ReviewQueue({ proposals, onReload }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [held, setHeld] = useState<Set<string>>(new Set());
  const [impacts, setImpacts] = useState<Record<string, Array<Record<string, unknown>>>>({});
  const [messages, setMessages] = useState<Record<string, string>>({});

  async function approve(id: string) {
    setBusy(id);
    try {
      const result = await api.commit(id);
      setMessages((value) => ({ ...value, [id]: `커밋 ${formatValue(result.status)}` }));
      await onReload();
    } catch (error) {
      setMessages((value) => ({ ...value, [id]: (error as Error).message }));
    } finally {
      setBusy(null);
    }
  }

  async function preview(id: string) {
    setBusy(id);
    try {
      const result = await api.impact(id);
      setImpacts((value) => ({ ...value, [id]: result.previews }));
    } catch (error) {
      setMessages((value) => ({ ...value, [id]: (error as Error).message }));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="board-view review-view" aria-labelledby="review-title">
      <header className="view-heading">
        <div><span className="eyebrow">PROPOSAL REVIEW</span><h1 id="review-title">검수 큐</h1></div>
        <p>도메인 필드별 diff와 read set을 함께 봅니다. 사람·에이전트·캐스케이드 출처는 저장 시점의 태그로 구분됩니다.</p>
      </header>
      <div className="review-queue">
        {proposals.map((proposal) => {
          const state = proposalState(proposal);
          const isHeld = held.has(proposal.id);
          return (
            <article className={`proposal-card${isHeld ? " is-held" : ""}`} key={proposal.id}>
              <header>
                <div className="proposal-title"><span className={`origin-badge ${proposal.actor_kind}`}>{ORIGIN_LABELS[proposal.actor_kind]}</span><h2>{proposal.rationale || "변경 제안"}</h2></div>
                <span className={`state-badge ${state.className}`}>{state.label}</span>
                <div className="proposal-meta"><code>{proposal.id}</code><span>{proposal.model_id || proposal.host || "unknown"} · {formatDate(proposal.ts)}</span></div>
              </header>
              {proposal.conflicts.length > 0 && (
                <div className="conflict-callout"><b>충돌로 보류됨</b><p>{formatValue(proposal.conflicts)}</p></div>
              )}
              <div className="operation-list">
                {proposal.ops.map((operation) => (
                  <div className={`operation verb-${operation.verb.toLowerCase()}`} key={operation.seq}>
                    <span className="operation-symbol">{verbSymbol(operation)}</span>
                    <div><code>{operation.target}{operation.field ? `.${operation.field}` : ""}</code>
                      {operation.verb === "UPDATE" ? (
                        <p className="inline-diff"><del>{formatValue(operation.from)}</del><span>→</span><ins>{formatValue(operation.to)}</ins></p>
                      ) : <pre>{formatValue(operation.to)}</pre>}
                    </div>
                  </div>
                ))}
              </div>
              <div className="read-set"><b>read set</b>{proposal.read_set.map((read) => <code key={`${read.node}-${read.rev}`}>{read.node}@r{read.rev}</code>)}</div>
              {impacts[proposal.id] && (
                <div className="impact-preview"><b>영향 미리보기</b>{impacts[proposal.id].map((preview, index) => (
                  <p key={index}><code>{String(preview.ref ?? "new")}</code> · 영향 {Array.isArray(preview.affected) ? preview.affected.length : 0}개 · 규칙 {Array.isArray(preview.broken_rules) ? preview.broken_rules.join(", ") || "없음" : "—"}</p>
                ))}</div>
              )}
              {messages[proposal.id] && <div className="proposal-message" role="status">{messages[proposal.id]}</div>}
              <footer>
                <button className="button primary" disabled={busy === proposal.id || proposal.conflicts.length > 0} onClick={() => void approve(proposal.id)}>승인</button>
                <button className="button" disabled={busy === proposal.id} onClick={() => void preview(proposal.id)}>영향 미리보기</button>
                <button className="button ghost" onClick={() => setHeld((values) => { const next = new Set(values); next.has(proposal.id) ? next.delete(proposal.id) : next.add(proposal.id); return next; })}>{isHeld ? "다시 보기" : "보류"}</button>
              </footer>
            </article>
          );
        })}
        {!proposals.length && <div className="empty-board"><span>✓</span><h2>검수 큐가 비었습니다</h2><p>열린 Proposal이 생기면 출처와 diff가 여기에 나타납니다.</p></div>}
      </div>
    </section>
  );
}
