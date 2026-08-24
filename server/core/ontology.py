"""Load and validate the machine-readable story ontology."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class OntologyError(ValueError):
    """Raised when ontology data or a graph value violates the contract."""


@dataclass(frozen=True, slots=True)
class KindSpec:
    name: str
    layer: str
    label: str
    props: tuple[str, ...]
    p0: bool = False
    internal: bool = False
    default_locked: bool = False


@dataclass(frozen=True, slots=True)
class EdgeSpec:
    rel: str
    group: str
    hard: bool
    src: str | None = None
    dst: str | None = None


class Ontology:
    """Validated ontology catalog backed by ``spec/ontology.json``."""

    def __init__(self, data: dict[str, Any], *, source: Path | None = None) -> None:
        self.source = source
        self.data = data
        self.version = self._required_string(data, "version")
        self.layers = self._load_layers(data.get("layers"))
        self.kinds = self._load_kinds(data.get("kinds"))
        self.edges = self._load_edges(data.get("edges"))
        self._kind_by_casefold = {name.casefold(): spec for name, spec in self.kinds.items()}
        self._validate_constraints()

    @classmethod
    def load(cls, path: str | Path) -> Ontology:
        source = Path(path).resolve()
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OntologyError(f"온톨로지를 읽을 수 없습니다: {source}: {exc}") from exc
        if not isinstance(data, dict):
            raise OntologyError("온톨로지 루트는 JSON 객체여야 합니다")
        return cls(data, source=source)

    @property
    def p0_kinds(self) -> tuple[str, ...]:
        """The six public node kinds allowed during P0."""
        return tuple(spec.name for spec in self.kinds.values() if spec.p0 and not spec.internal)

    def canonical_kind(self, kind: str, *, p0_only: bool = False) -> str:
        if not isinstance(kind, str) or not kind.strip():
            raise OntologyError("kind는 비어 있지 않은 문자열이어야 합니다")
        spec = self._kind_by_casefold.get(kind.strip().casefold())
        if spec is None:
            allowed = ", ".join(self.kinds)
            raise OntologyError(f"알 수 없는 노드 타입 {kind!r}. 허용: {allowed}")
        if p0_only and (not spec.p0 or spec.internal):
            allowed = ", ".join(self.p0_kinds)
            raise OntologyError(f"P0에서 사용할 수 없는 노드 타입 {spec.name!r}. 허용: {allowed}")
        return spec.name

    def validate_edge(self, rel: str, src_kind: str, dst_kind: str) -> EdgeSpec:
        try:
            spec = self.edges[rel]
        except KeyError as exc:
            raise OntologyError(f"알 수 없는 간선 타입 {rel!r}") from exc
        source = self.canonical_kind(src_kind)
        target = self.canonical_kind(dst_kind)
        if spec.src and spec.src != source:
            raise OntologyError(f"{rel}의 src는 {spec.src}여야 합니다: {source}")
        if spec.dst and spec.dst != target and spec.dst != "span":
            raise OntologyError(f"{rel}의 dst는 {spec.dst}여야 합니다: {target}")
        return spec

    def schema(self, section: str | None = None) -> dict[str, Any]:
        allowed = {"kinds", "edges", "tags", "rules"}
        if section is not None and section not in allowed:
            raise OntologyError(f"section은 {sorted(allowed)} 중 하나여야 합니다")
        result = {
            "kinds": self.data.get("kinds", []),
            "edges": self.data.get("edges", []),
            "tags": [],
            "rules": [],
        }
        if section is None:
            return result
        return {section: result[section]}

    @staticmethod
    def _required_string(data: dict[str, Any], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise OntologyError(f"{key}는 비어 있지 않은 문자열이어야 합니다")
        return value

    @staticmethod
    def _load_layers(raw: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(raw, dict) or not raw:
            raise OntologyError("layers는 비어 있지 않은 객체여야 합니다")
        for name, value in raw.items():
            if not isinstance(name, str) or not isinstance(value, dict):
                raise OntologyError("layers 항목 형식이 잘못되었습니다")
        return raw

    def _load_kinds(self, raw: Any) -> dict[str, KindSpec]:
        if not isinstance(raw, list) or not raw:
            raise OntologyError("kinds는 비어 있지 않은 배열이어야 합니다")
        result: dict[str, KindSpec] = {}
        for item in raw:
            if not isinstance(item, dict):
                raise OntologyError("kind 항목은 객체여야 합니다")
            name = self._required_string(item, "name")
            if name in result:
                raise OntologyError(f"중복 kind: {name}")
            layer = self._required_string(item, "layer")
            label = self._required_string(item, "label")
            props = item.get("props", [])
            if layer not in self.layers:
                raise OntologyError(f"{name}이 알 수 없는 layer {layer!r}를 사용합니다")
            if not isinstance(props, list) or not all(isinstance(x, str) for x in props):
                raise OntologyError(f"{name}.props는 문자열 배열이어야 합니다")
            result[name] = KindSpec(
                name=name,
                layer=layer,
                label=label,
                props=tuple(props),
                p0=bool(item.get("p0", False)),
                internal=bool(item.get("internal", False)),
                default_locked=bool(item.get("default_locked", False)),
            )
        return result

    def _load_edges(self, raw: Any) -> dict[str, EdgeSpec]:
        if not isinstance(raw, list) or not raw:
            raise OntologyError("edges는 비어 있지 않은 배열이어야 합니다")
        result: dict[str, EdgeSpec] = {}
        for item in raw:
            if not isinstance(item, dict):
                raise OntologyError("edge 항목은 객체여야 합니다")
            rel = self._required_string(item, "rel")
            if rel in result:
                raise OntologyError(f"중복 edge: {rel}")
            result[rel] = EdgeSpec(
                rel=rel,
                group=self._required_string(item, "group"),
                hard=bool(item.get("hard", True)),
                src=item.get("src"),
                dst=item.get("dst"),
            )
        return result

    def _validate_constraints(self) -> None:
        for spec in self.edges.values():
            if spec.src and spec.src not in self.kinds:
                raise OntologyError(f"{spec.rel}.src가 알 수 없는 kind입니다: {spec.src}")
            if spec.dst and spec.dst != "span" and spec.dst not in self.kinds:
                raise OntologyError(f"{spec.rel}.dst가 알 수 없는 kind입니다: {spec.dst}")
