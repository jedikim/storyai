import type { ProjectList } from "../types";

interface ProjectSwitcherProps {
  projects: ProjectList;
  busy: boolean;
  onSelect: (name: string) => void;
}

export function ProjectSwitcher({ projects, busy, onSelect }: ProjectSwitcherProps) {
  return (
    <label className="project-switcher" title="현재 작업할 소설 프로젝트">
      <span>PROJECT</span>
      <select
        aria-label="소설 프로젝트"
        value={projects.selected}
        disabled={busy || projects.projects.length === 0}
        onChange={(event) => onSelect(event.target.value)}
      >
        {projects.projects.length === 0 && <option value="">불러오는 중…</option>}
        {projects.projects.map((item) => (
          <option key={item.name} value={item.name} disabled={!item.available}>
            {item.name}{item.available ? "" : " (사용 불가)"}
          </option>
        ))}
      </select>
    </label>
  );
}
