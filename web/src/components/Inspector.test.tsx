import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import type { NodeDetail } from "../types";
import { Inspector } from "./Inspector";

vi.mock("../api", () => ({
  api: {
    node: vi.fn(),
    updateSummary: vi.fn(),
  },
}));

const detail: NodeDetail = {
  id: "object/청동열쇠",
  kind: "Object",
  layer: "substance",
  title: "청동 열쇠",
  summary: "기존 설명",
  props: { 재질: "청동" },
  tags: [],
  story_from: null,
  story_to: null,
  reveal_at: null,
  origin: "agent",
  locked: false,
  rev: 2,
  diagnostics: 0,
  aliases: [],
  features: {},
  visible_to: [],
  body: "청동 열쇠",
  evidence: [],
  refs: [],
  history: [],
};

describe("Inspector summary editor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.node).mockResolvedValue(detail);
  });

  it("edits and saves a node summary at the displayed revision", async () => {
    const onUpdated = vi.fn();
    vi.mocked(api.updateSummary).mockResolvedValue({
      proposal_id: "proposal/ui-summary",
      status: "accepted",
      node: { ...detail, summary: "새 상세 설명", rev: 3, origin: "human" },
    });
    render(
      <Inspector
        nodeId={detail.id}
        asOf={1}
        maxChapter={1}
        onSelect={vi.fn()}
        onClose={vi.fn()}
        onUpdated={onUpdated}
      />,
    );

    expect(await screen.findByText("기존 설명")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "편집" }));
    fireEvent.change(screen.getByRole("textbox", { name: "노드 설명" }), {
      target: { value: "새 상세 설명" },
    });
    fireEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(api.updateSummary).toHaveBeenCalledWith(detail.id, 2, "새 상세 설명"));
    expect(await screen.findByText("새 상세 설명")).toBeInTheDocument();
    expect(screen.getByText("저장했습니다.")).toBeInTheDocument();
    expect(onUpdated).toHaveBeenCalledWith(expect.objectContaining({ rev: 3 }));
  });
});
