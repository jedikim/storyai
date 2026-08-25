import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PromiseBoard } from "./PromiseBoard";

describe("PromiseBoard", () => {
  it("renders every F-T-P state and selects a promise", () => {
    const onSelect = vi.fn();
    render(
      <PromiseBoard
        onSelect={onSelect}
        items={[{
          id: "promise/red-thread",
          title: "붉은 실",
          F: ["scene/seed"],
          T: ["scene/trigger"],
          P: ["scene/payoff"],
          status: "eligible",
          debt: 0.7,
          s_eff: 0.8,
          delta_coh: 0.4,
        }]}
      />,
    );

    expect(screen.getByText("가설")).toBeInTheDocument();
    expect(screen.getByText("회수 가능")).toBeInTheDocument();
    expect(screen.getByText("회수 완료")).toBeInTheDocument();
    expect(screen.getByText("의도적 폐기")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /붉은 실/ }));
    expect(onSelect).toHaveBeenCalledWith("promise/red-thread");
  });
});
