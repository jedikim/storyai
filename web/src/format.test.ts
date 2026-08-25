import { describe, expect, it } from "vitest";

import { formatBytes, formatValue, proposalState } from "./format";
import type { Proposal } from "./types";

function proposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    id: "proposal/test",
    status: "open",
    risk: "auto",
    reasons: [],
    conflicts: [],
    pending_overlap: [],
    actor_kind: "agent",
    model_id: "codex",
    session_id: null,
    host: "test",
    rationale: "test",
    read_set: [],
    ts: "2026-08-25T00:00:00Z",
    ops: [],
    ...overrides,
  };
}

describe("format helpers", () => {
  it("formats empty, structured, and byte values", () => {
    expect(formatValue(null)).toBe("—");
    expect(formatValue({ F: "seed" })).toContain('"F": "seed"');
    expect(formatBytes(1536)).toBe("1.5 KB");
  });

  it("prioritizes conflicts over risk labels", () => {
    expect(proposalState(proposal()).label).toBe("자동 승인 가능");
    expect(proposalState(proposal({ risk: "review" })).label).toBe("검토 필요");
    expect(proposalState(proposal({ conflicts: [{ node: "fact/x" }] })).label).toBe("충돌");
  });
});
