from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from server.core.projects import ProjectRegistry
from server.runtime import get_project_registry, manage_project, reset_service


def _registry(project: Path, registry_path: Path) -> ProjectRegistry:
    return ProjectRegistry(
        registry_path=registry_path,
        default_root=project,
        default_db=project / "store" / "story.db",
        default_name="default",
        template_root=project,
    )


def test_project_create_is_idempotent_and_persists_selection(
    project: Path,
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry" / "projects.json"
    target = tmp_path / "created-novel"
    registry = _registry(project, registry_path)

    created = registry.manage(mode="create", name="새소설", path=str(target))
    repeated = registry.manage(mode="create", name="새소설", path=str(target))
    reloaded = _registry(project, registry_path)

    assert created["project"]["root"] == str(target)
    assert repeated["selected"] == "새소설"
    assert reloaded.current()["name"] == "새소설"
    assert (target / "spec" / "schema.sql").is_file()
    assert json.loads((target / ".storyai" / "project.json").read_text())["name"] == "새소설"
    assert registry_path.stat().st_mode & 0o777 == 0o600


def test_project_registry_rejects_ambiguous_or_unsafe_inputs(
    project: Path,
    tmp_path: Path,
) -> None:
    registry = _registry(project, tmp_path / "projects.json")
    target = tmp_path / "novel"
    registry.manage(mode="create", name="novel", path=str(target))

    with pytest.raises(ValueError, match="절대 경로"):
        registry.manage(mode="create", name="relative", path="relative/novel")
    with pytest.raises(ValueError, match="marker의 이름"):
        registry.manage(mode="create", name="other", path=str(target))
    with pytest.raises(ValueError, match="name"):
        registry.manage(mode="select", name="../escape")
    with pytest.raises(ValueError, match="사용할 수 없습니다"):
        registry.manage(mode="list", path=str(target))

    incomplete = tmp_path / "incomplete"
    shutil.copytree(project / "spec", incomplete / "spec")
    with pytest.raises(ValueError, match="디렉터리가 불완전"):
        registry.manage(mode="register", name="incomplete", path=str(incomplete))

    state = json.loads((tmp_path / "projects.json").read_text(encoding="utf-8"))
    state["projects"]["incomplete"] = {
        "root": str(incomplete),
        "db": str(incomplete / "store" / "story.db"),
    }
    (tmp_path / "projects.json").write_text(json.dumps(state), encoding="utf-8")
    listed = registry.manage(mode="list")
    assert (
        next(item for item in listed["projects"] if item["name"] == "incomplete")["available"]
        is False
    )


def test_project_registry_fails_closed_on_invalid_selected_entry(
    project: Path,
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "projects.json"
    registry_path.write_text(
        json.dumps(
            {
                "version": 1,
                "selected": "missing",
                "projects": {
                    "default": {
                        "root": str(project),
                        "db": str(project / "store" / "story.db"),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="선택된 프로젝트"):
        _registry(project, registry_path).current()


def test_runtime_rolls_back_selection_when_service_initialization_fails(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "runtime-projects.json"
    broken = tmp_path / "broken-project"
    shutil.copytree(project / "spec", broken / "spec")
    for folder in ("manuscript", "bible", "store"):
        (broken / folder).mkdir()
    (broken / "spec" / "ontology.json").write_text("not-json", encoding="utf-8")
    monkeypatch.setenv("STORYAI_PROJECT_ROOT", str(project))
    monkeypatch.setenv("STORYAI_DB", str(project / "store" / "story.db"))
    monkeypatch.setenv("STORYAI_PROJECTS_FILE", str(registry_path))
    reset_service()
    try:
        with pytest.raises(ValueError, match="온톨로지"):
            manage_project(mode="register", name="broken", path=str(broken))
        assert get_project_registry().current()["name"] == "storyai"
    finally:
        reset_service()
