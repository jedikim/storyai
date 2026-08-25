import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ReviewQueue } from "./ReviewQueue";
import type { Proposal } from "../types";

describe("ReviewQueue", () => {
  it("shows provenance, read set, and field-level diff", () => {
    const item: Proposal = {
      id: "proposal/reveal",
      status: "open",
      risk: "review",
      reasons: ["canon field"],
      conflicts: [],
      pending_overlap: [],
      actor_kind: "cascade",
      model_id: "codex",
      session_id: "session/test",
      host: "test",
      rationale: "폭로 시점 조정",
      read_set: [{ node: "reveal/name", rev: 2 }],
      ts: "2026-08-25T00:00:00Z",
      ops: [{ seq: 0, verb: "UPDATE", target: "reveal/name", field: "reveal_at", from: 8, to: 7, basis_rev: 2, idem_key: "change" }],
    };
    render(<ReviewQueue proposals={[item]} onReload={vi.fn()} />);

    expect(screen.getByText("캐스케이드")).toBeInTheDocument();
    expect(screen.getByText("reveal/name@r2")).toBeInTheDocument();
    expect(screen.getByText("8").tagName).toBe("DEL");
    expect(screen.getByText("7").tagName).toBe("INS");
  });
});
