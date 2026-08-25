import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TimelineView } from "./TimelineView";

describe("TimelineView", () => {
  it("marks flashbacks and supports keyboard selection", () => {
    const onSelect = vi.fn();
    render(
      <TimelineView
        onSelect={onSelect}
        timeline={{
          max_chapter: 8,
          points: [{ id: "scene/flashback", kind: "Scene", title: "겨울의 기억", story: 2, story_to: null, discourse: 7, flashback: true }],
        }}
      />,
    );

    expect(screen.getByText("↺ 회상")).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("button"), { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("scene/flashback");
  });
});
