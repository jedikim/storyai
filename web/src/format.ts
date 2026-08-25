import type { Layer, Origin, Proposal } from "./types";

export const KIND_LABELS: Record<string, string> = {
  Promise: "복선",
  Twist: "반전",
  Reveal: "폭로",
  Fact: "사실",
  Thread: "플롯라인",
  Arc: "인물 아크",
  Session: "세션",
  Scene: "씬",
  Event: "사건",
  Beat: "비트",
  Chapter: "장",
  Act: "막",
  Character: "인물",
  Location: "장소",
  Object: "소품",
  Faction: "집단",
  Rule: "세계 규칙",
  Concept: "개념",
};

export const LAYER_LABELS: Record<Layer, string> = {
  device: "서사 장치",
  event: "사건",
  substance: "원소",
};

export const ORIGIN_LABELS: Record<Origin, string> = {
  human: "사람",
  agent: "에이전트",
  cascade: "캐스케이드",
};

export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value, null, 2);
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDate(value: string | null): string {
  if (!value) return "아직 없음";
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function proposalState(proposal: Proposal): {
  label: string;
  className: "ok" | "warn" | "bad";
} {
  if (proposal.conflicts.length) return { label: "충돌", className: "bad" };
  if (proposal.risk === "auto") return { label: "자동 승인 가능", className: "ok" };
  if (proposal.risk === "review") return { label: "검토 필요", className: "warn" };
  return { label: "사람 승인 필수", className: "bad" };
}
