import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ProjectSwitcher } from "./ProjectSwitcher";

describe("ProjectSwitcher", () => {
  it("shows the selected project, disables unavailable projects, and switches", () => {
    const onSelect = vi.fn();
    render(
      <ProjectSwitcher
        busy={false}
        onSelect={onSelect}
        projects={{
          mode: "list",
          selected: "storyai",
          projects: [
            {
              name: "storyai",
              selected: true,
              available: true,
            },
            {
              name: "유리등대",
              selected: false,
              available: true,
            },
            {
              name: "삭제된 소설",
              selected: false,
              available: false,
            },
          ],
        }}
      />,
    );

    const selector = screen.getByRole("combobox", { name: "소설 프로젝트" });
    expect(selector).toHaveValue("storyai");
    expect(screen.getByRole("option", { name: "삭제된 소설 (사용 불가)" })).toBeDisabled();
    fireEvent.change(selector, { target: { value: "유리등대" } });
    expect(onSelect).toHaveBeenCalledWith("유리등대");
  });
});
