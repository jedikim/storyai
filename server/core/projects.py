"""Persistent multi-project registry for one storyai MCP process."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal

from .merkle import canonical_json

ProjectMode = Literal["current", "list", "create", "register", "select"]

_PROJECT_FILES = ("ontology.json", "policy.json", "rules.json", "schema.sql", "tools.json")
_PROJECT_DIRS = ("manuscript", "bible", "store")
_NAME_PATTERN = re.compile(r"^[^/\\\x00-\x1f]{1,100}$")


class ProjectRegistry:
    """Store named project roots and the selected project in a small JSON file."""

    def __init__(
        self,
        *,
        registry_path: str | Path,
        default_root: str | Path,
        default_db: str | Path,
        default_name: str,
        template_root: str | Path,
    ) -> None:
        self.registry_path = Path(registry_path).expanduser().resolve()
        self.default_root = Path(default_root).expanduser().resolve()
        self.default_db = Path(default_db).expanduser().resolve()
        self.default_name = self._name(default_name)
        self.template_root = Path(template_root).expanduser().resolve()
        self._lock = threading.RLock()

    @classmethod
    def from_environment(cls) -> ProjectRegistry:
        default_root = Path(__file__).resolve().parents[2]
        root = Path(os.environ.get("STORYAI_PROJECT_ROOT", default_root)).expanduser().resolve()
        raw_db = os.environ.get("STORYAI_DB", str(root / "store" / "story.db"))
        database = Path(raw_db.replace("${PROJECT_DIR}", str(root))).expanduser().resolve()
        registry = os.environ.get("STORYAI_PROJECTS_FILE", str(root / ".storyai" / "projects.json"))
        template = os.environ.get("STORYAI_TEMPLATE_ROOT", str(root))
        name = os.environ.get("STORYAI_PROJECT_NAME", root.name or "default")
        return cls(
            registry_path=registry,
            default_root=root,
            default_db=database,
            default_name=name,
            template_root=template,
        )

    def manage(
        self,
        *,
        mode: ProjectMode,
        name: str | None = None,
        path: str | None = None,
    ) -> dict[str, Any]:
        if mode not in {"current", "list", "create", "register", "select"}:
            raise ValueError(
                "project.mode는 current, list, create, register, select 중 하나여야 합니다"
            )
        if mode in {"create", "register", "select"}:
            if name is None:
                raise ValueError(f"project.{mode}에는 name이 필요합니다")
            name = self._name(name)
        elif name is not None:
            raise ValueError(f"project.{mode}에는 name을 사용할 수 없습니다")
        if mode in {"create", "register"}:
            if path is None:
                raise ValueError(f"project.{mode}에는 절대 path가 필요합니다")
        elif path is not None:
            raise ValueError(f"project.{mode}에는 path를 사용할 수 없습니다")

        if mode == "current":
            state = self._load()
            return {
                "mode": mode,
                "selected": state["selected"],
                "project": self._current_from_state(state),
            }
        if mode == "list":
            state = self._load()
            return {
                "mode": mode,
                "selected": state["selected"],
                "projects": [
                    self._public(project_name, entry, selected=project_name == state["selected"])
                    for project_name, entry in sorted(state["projects"].items())
                ],
            }
        if mode == "create":
            assert name is not None and path is not None
            root = self._absolute_path(path)
            self._create_files(name, root)
            return self._register(name, root, mode="create")
        if mode == "register":
            assert name is not None and path is not None
            return self._register(name, self._absolute_path(path), mode="register")

        assert name is not None
        with self._lock:
            state = self._load()
            entry = state["projects"].get(name)
            if entry is None:
                raise ValueError(f"등록된 프로젝트를 찾을 수 없습니다: {name}")
            self._validate_project(Path(entry["root"]))
            state["selected"] = name
            self._save(state)
        return {
            "mode": "select",
            "selected": name,
            "project": self._public(name, entry, selected=True),
        }

    def current(self) -> dict[str, Any]:
        return self._current_from_state(self._load())

    def _current_from_state(self, state: dict[str, Any]) -> dict[str, Any]:
        selected = str(state["selected"])
        entry = state["projects"].get(selected)
        if entry is None:
            raise ValueError(f"선택된 프로젝트가 레지스트리에 없습니다: {selected}")
        self._validate_project(Path(entry["root"]))
        return self._public(selected, entry, selected=True)

    def selected_name(self) -> str:
        """Return the registry pointer without requiring the target to be available."""
        return str(self._load()["selected"])

    def restore_selection(self, name: str) -> None:
        """Restore a prior pointer after target service initialization failed."""
        with self._lock:
            state = self._load()
            if name not in state["projects"]:
                raise ValueError(f"복원할 프로젝트가 레지스트리에 없습니다: {name}")
            state["selected"] = name
            self._save(state)

    def _register(self, name: str, root: Path, *, mode: str) -> dict[str, Any]:
        self._validate_project(root)
        database = root / "store" / "story.db"
        with self._lock:
            state = self._load()
            existing = state["projects"].get(name)
            entry = {"root": str(root), "db": str(database)}
            if existing is not None and existing != entry:
                raise ValueError(f"같은 이름이 다른 프로젝트에 등록되어 있습니다: {name}")
            for other_name, other in state["projects"].items():
                if other_name != name and Path(other["root"]).resolve() == root:
                    raise ValueError(f"프로젝트 경로가 이미 등록되어 있습니다: {other_name}")
            state["projects"][name] = entry
            state["selected"] = name
            self._save(state)
        return {
            "mode": mode,
            "selected": name,
            "project": self._public(name, entry, selected=True),
        }

    def _create_files(self, name: str, root: Path) -> None:
        marker = root / ".storyai" / "project.json"
        if root.exists():
            if not root.is_dir():
                raise ValueError(f"프로젝트 path가 디렉터리가 아닙니다: {root}")
            if marker.is_file():
                try:
                    payload = json.loads(marker.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ValueError(f"프로젝트 marker를 읽을 수 없습니다: {marker}") from exc
                if payload.get("name") != name:
                    raise ValueError(f"기존 프로젝트 marker의 이름이 다릅니다: {root}")
                self._validate_project(root)
                return
            if any(root.iterdir()):
                raise ValueError(f"비어 있지 않은 경로에는 프로젝트를 만들 수 없습니다: {root}")
            root.rmdir()

        template_spec = self.template_root / "spec"
        missing = [item for item in _PROJECT_FILES if not (template_spec / item).is_file()]
        if missing:
            raise ValueError(f"프로젝트 template spec이 불완전합니다: {', '.join(missing)}")
        root.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{root.name}.storyai-", dir=root.parent))
        try:
            (temporary / "spec").mkdir()
            for item in _PROJECT_FILES:
                shutil.copy2(template_spec / item, temporary / "spec" / item)
            for folder in ("manuscript", "bible", "store"):
                (temporary / folder).mkdir()
            (temporary / ".storyai").mkdir()
            (temporary / ".storyai" / "project.json").write_text(
                canonical_json({"version": 1, "name": name}) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, root)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def _load(self) -> dict[str, Any]:
        default = {
            "version": 1,
            "selected": self.default_name,
            "projects": {
                self.default_name: {"root": str(self.default_root), "db": str(self.default_db)}
            },
        }
        if not self.registry_path.is_file():
            return default
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"프로젝트 레지스트리를 읽을 수 없습니다: {self.registry_path}"
            ) from exc
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("프로젝트 레지스트리 version은 1이어야 합니다")
        projects = payload.get("projects")
        selected = payload.get("selected")
        if not isinstance(projects, dict) or not projects or not isinstance(selected, str):
            raise ValueError("프로젝트 레지스트리 구조가 잘못되었습니다")
        normalized: dict[str, dict[str, str]] = {}
        roots: dict[Path, str] = {}
        for raw_name, raw_entry in projects.items():
            project_name = self._name(raw_name)
            if project_name in normalized:
                raise ValueError(f"정규화 후 중복된 프로젝트 이름입니다: {project_name}")
            if not isinstance(raw_entry, dict):
                raise ValueError(f"프로젝트 레지스트리 항목이 잘못되었습니다: {project_name}")
            root = self._stored_path(raw_entry.get("root"), "root")
            database = self._stored_path(raw_entry.get("db"), "db")
            if root in roots:
                raise ValueError(
                    f"프로젝트 경로가 중복 등록되어 있습니다: {roots[root]}, {project_name}"
                )
            roots[root] = project_name
            normalized[project_name] = {"root": str(root), "db": str(database)}
        if selected not in normalized:
            raise ValueError(f"선택된 프로젝트가 레지스트리에 없습니다: {selected}")
        return {"version": 1, "selected": selected, "projects": normalized}

    def _save(self, state: dict[str, Any]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=f".{self.registry_path.name}.",
            suffix=".tmp",
            dir=self.registry_path.parent,
        )
        temporary = Path(raw_temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(canonical_json(state) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.registry_path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _public(name: str, entry: dict[str, str], *, selected: bool) -> dict[str, Any]:
        root = Path(entry["root"])
        available = root.is_dir() and all(
            (root / "spec" / item).is_file() for item in _PROJECT_FILES
        )
        return {
            "name": name,
            "root": str(root),
            "db": str(Path(entry["db"])),
            "selected": selected,
            "available": available,
        }

    @staticmethod
    def _name(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("project.name은 문자열이어야 합니다")
        name = value.strip()
        if name in {"", ".", ".."} or _NAME_PATTERN.fullmatch(name) is None:
            raise ValueError(
                "project.name은 구분자나 제어 문자가 없는 1..100자 문자열이어야 합니다"
            )
        return name

    @staticmethod
    def _absolute_path(value: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("project.path는 비어 있지 않은 문자열이어야 합니다")
        raw = Path(value.strip()).expanduser()
        if not raw.is_absolute():
            raise ValueError("project.path는 절대 경로여야 합니다")
        return raw.resolve()

    @staticmethod
    def _stored_path(value: Any, field: str) -> Path:
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f"프로젝트 레지스트리 {field}는 절대 경로여야 합니다")
        return Path(value).expanduser().resolve()

    @staticmethod
    def _validate_project(root: Path) -> None:
        if not root.is_dir():
            raise ValueError(f"프로젝트 디렉터리를 찾을 수 없습니다: {root}")
        missing_dirs = [item for item in _PROJECT_DIRS if not (root / item).is_dir()]
        if missing_dirs:
            raise ValueError(f"storyai 프로젝트 디렉터리가 불완전합니다: {', '.join(missing_dirs)}")
        missing = [item for item in _PROJECT_FILES if not (root / "spec" / item).is_file()]
        if missing:
            raise ValueError(f"storyai 프로젝트 spec이 불완전합니다: {', '.join(missing)}")
