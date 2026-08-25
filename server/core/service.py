"""Application service for the deterministic P0-P5 graph tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from .address import AddressResolver
from .budget import DEFAULT_MAX_CHARS, fit_response
from .consolidate import OfflineConsolidator
from .database import initialize_database
from .diagnostics import DiagnosticEngine
from .embedding import EmbeddingIndex
from .graph_tools import GraphAnalysis, QueryService
from .ingest import IngestService
from .ontology import Ontology, OntologyError
from .p2 import ContractService, PromiseService, VisibilityService
from .search import HybridSearch
from .traverse import GraphStore
from .write_service import WriteService

ResponseFormat = Literal["concise", "detailed"]


class StoryService:
    def __init__(
        self,
        *,
        project_root: str | Path,
        db_path: str | Path,
        ontology_path: str | Path,
        rules_path: str | Path,
        schema_path: str | Path,
        policy_path: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.db_path = initialize_database(db_path, schema_path)
        self.ontology = Ontology.load(ontology_path)
        self.rules_path = Path(rules_path).resolve()
        self.store = GraphStore(self.db_path, project_root=self.project_root)
        self.addresses = AddressResolver(self.ontology, self.store)
        self.diagnostics = DiagnosticEngine(self.db_path, self.rules_path)
        self.promise_store = PromiseService(self.db_path)
        self.contracts = ContractService(self.db_path)
        self.visibility_store = VisibilityService(self.db_path)
        self.writer = WriteService(
            db_path=self.db_path,
            ontology=self.ontology,
            policy_path=policy_path or self.project_root / "spec" / "policy.json",
            rules_path=self.rules_path,
        )
        self.embeddings = EmbeddingIndex(self.db_path)
        self.embeddings.sync_all()
        self.search = HybridSearch(
            db_path=self.db_path,
            graph=self.store,
            embeddings=self.embeddings,
        )
        self.analysis = GraphAnalysis(
            db_path=self.db_path,
            graph=self.store,
            search=self.search,
            diagnostics=self.diagnostics,
            device_kinds={
                name for name, item in self.ontology.kinds.items() if item.layer == "device"
            },
        )
        self.query_store = QueryService(self.db_path)
        self.ingester = IngestService(
            project_root=self.project_root,
            db_path=self.db_path,
            ontology=self.ontology,
            writer=self.writer,
        )
        self.consolidator = OfflineConsolidator(self.db_path, self.embeddings)

    @classmethod
    def from_environment(cls) -> StoryService:
        default_root = Path(__file__).resolve().parents[2]
        root = Path(os.environ.get("STORYAI_PROJECT_ROOT", default_root)).expanduser().resolve()
        raw_db = os.environ.get("STORYAI_DB", str(root / "store" / "story.db"))
        raw_db = raw_db.replace("${PROJECT_DIR}", str(root))
        return cls(
            project_root=root,
            db_path=raw_db,
            ontology_path=root / "spec" / "ontology.json",
            rules_path=root / "spec" / "rules.json",
            schema_path=root / "spec" / "schema.sql",
            policy_path=root / "spec" / "policy.json",
        )

    def propose(
        self,
        *,
        ops: list[dict[str, Any]],
        read_set: list[dict[str, Any]],
        rationale: str,
        session_id: str,
        actor_kind: Literal["human", "agent", "cascade"] = "agent",
        model_id: str | None = None,
        host: Literal["claude-code", "codex", "ui", "test"] = "codex",
        on_behalf_of: str | None = None,
    ) -> dict[str, Any]:
        return self.writer.propose(
            ops=ops,
            read_set=read_set,
            rationale=rationale,
            session_id=session_id,
            actor_kind=actor_kind,
            model_id=model_id,
            host=host,
            on_behalf_of=on_behalf_of,
        )

    def commit(
        self,
        proposal_id: str,
        *,
        mode: Literal["apply", "dry_run"] = "apply",
        allow_cycles: bool = False,
        max_iterations: int | None = None,
    ) -> dict[str, Any]:
        result = self.writer.commit(
            proposal_id,
            mode=mode,
            allow_cycles=allow_cycles,
            max_iterations=max_iterations,
        )
        if result["status"] == "accepted":
            self.embeddings.sync_all()
        return result

    def check(
        self,
        scope: str = "book",
        *,
        rules: list[str] | None = None,
        severity: Literal["error", "warn", "info"] | None = None,
        response_format: ResponseFormat = "concise",
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> Any:
        self._response_format(response_format)
        resolved_scope = (
            None if scope in {"book", "story://book"} else self.addresses.resolve(scope)
        )
        result = self.diagnostics.check(
            scope=resolved_scope,
            rule_ids=rules,
            severity=severity,
        )
        return fit_response(result, max_chars)

    def promises(
        self,
        *,
        status: list[str] | None = None,
        as_of: int | None = None,
        sort: Literal["debt", "age", "s_eff"] = "debt",
        response_format: ResponseFormat = "concise",
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> Any:
        self._response_format(response_format)
        result = self.promise_store.list(statuses=status, as_of=as_of, sort=sort)
        return fit_response(result, max_chars)

    def feasible(self, scene: str, *, as_of: int | None = None) -> dict[str, Any]:
        return self.contracts.feasible(self.addresses.resolve(scene), as_of=as_of)

    def visibility(
        self,
        fact: str,
        *,
        character: str | None = None,
        as_of: int = 0,
        spoken: bool = False,
    ) -> dict[str, Any]:
        resolved_character = self.addresses.resolve(character) if character is not None else None
        return self.visibility_store.classify(
            self.addresses.resolve(fact),
            character=resolved_character,
            as_of=as_of,
            spoken=spoken,
        )

    def graph_schema(
        self,
        section: Literal["kinds", "edges", "tags", "rules"] | None = None,
        *,
        response_format: ResponseFormat = "concise",
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> Any:
        self._response_format(response_format)
        result = self.ontology.schema(section)
        if section in {None, "tags"}:
            result["tags"] = self.store.tags()
        if section in {None, "rules"}:
            rules = self._load_rules()
            result["rules"] = rules.get("rules", [])
        if response_format == "concise":
            result = self._concise_schema(result)
        return fit_response(result, max_chars)

    def outline(
        self,
        scope: str = "book",
        *,
        depth: int = 1,
        kind: list[str] | None = None,
        response_format: ResponseFormat = "concise",
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> Any:
        self._response_format(response_format)
        if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= 20:
            raise ValueError("depth는 0~20 범위의 정수여야 합니다")
        kinds = self._kinds(kind)
        resolved_scope = None if scope == "book" else self.addresses.resolve(scope)
        result = self.store.outline(resolved_scope, depth=depth, kinds=kinds)
        return fit_response(result, max_chars)

    def find(
        self,
        q: str,
        *,
        kind: list[str] | None = None,
        tag: list[str] | None = None,
        as_of: int | None = None,
        mode: Literal["lexical", "semantic", "hybrid"] = "hybrid",
        limit: int = 20,
        response_format: ResponseFormat = "concise",
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> Any:
        self._response_format(response_format)
        if not isinstance(q, str) or not q.strip():
            raise ValueError("q는 비어 있지 않은 문자열이어야 합니다")
        if mode not in {"lexical", "semantic", "hybrid"}:
            raise ValueError("mode는 lexical, semantic, hybrid 중 하나여야 합니다")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit은 1~100 범위의 정수여야 합니다")
        self._as_of(as_of)
        result = self.search.find(
            q.strip(),
            kinds=self._kinds(kind),
            tags=self._tags(tag),
            as_of=as_of,
            mode=mode,
            limit=limit,
        )
        return fit_response(result, max_chars)

    def trace(
        self,
        source: str,
        *,
        target: str | None = None,
        via: list[str] | None = None,
        max_depth: int = 5,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        if (
            not isinstance(max_depth, int)
            or isinstance(max_depth, bool)
            or not 1 <= max_depth <= 20
        ):
            raise ValueError("max_depth는 1~20 범위의 정수여야 합니다")
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= 50:
            raise ValueError("k는 1~50 범위의 정수여야 합니다")
        relations = list(via or [])
        if not all(isinstance(item, str) for item in relations):
            raise ValueError("via는 간선 이름 배열이어야 합니다")
        unknown = sorted(set(relations) - set(self.ontology.edges))
        if unknown:
            raise OntologyError(f"알 수 없는 간선 타입: {', '.join(unknown)}")
        return self.analysis.trace(
            self.addresses.resolve(source),
            target=self.addresses.resolve(target) if target is not None else None,
            relations=relations,
            max_depth=max_depth,
            k=k,
        )

    def neighborhood(
        self,
        intent: str,
        *,
        anchors: list[str] | None = None,
        as_of: int | None = None,
        budget_tokens: int = 4_000,
    ) -> dict[str, Any]:
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("intent는 비어 있지 않은 문자열이어야 합니다")
        if (
            not isinstance(budget_tokens, int)
            or isinstance(budget_tokens, bool)
            or not 1 <= budget_tokens <= 100_000
        ):
            raise ValueError("budget_tokens는 1~100000 범위의 정수여야 합니다")
        self._as_of(as_of)
        values = anchors or []
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ValueError("anchors는 노드 주소 배열이어야 합니다")
        return self.analysis.neighborhood(
            intent.strip(),
            anchors=[self.addresses.resolve(item) for item in values],
            as_of=as_of,
            budget_tokens=budget_tokens,
        )

    def impact(
        self,
        ref: str,
        *,
        change: dict[str, Any],
        max_depth: int = 3,
    ) -> dict[str, Any]:
        if not isinstance(change, dict):
            raise ValueError("change는 field와 to를 가진 객체여야 합니다")
        if (
            not isinstance(max_depth, int)
            or isinstance(max_depth, bool)
            or not 1 <= max_depth <= 20
        ):
            raise ValueError("max_depth는 1~20 범위의 정수여야 합니다")
        return self.analysis.impact(
            self.addresses.resolve(ref),
            change=change,
            max_depth=max_depth,
        )

    def query(
        self,
        sql: str,
        *,
        params: dict[str, Any] | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        if not isinstance(sql, str):
            raise ValueError("sql은 문자열이어야 합니다")
        if params is not None and not isinstance(params, dict):
            raise ValueError("params는 이름 기반 파라미터 객체여야 합니다")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1_000:
            raise ValueError("limit은 1~1000 범위의 정수여야 합니다")
        return self.query_store.execute(sql, params=params, limit=limit)

    def ingest(
        self,
        chapter: str,
        *,
        mode: Literal["extract", "reindex"] = "extract",
    ) -> dict[str, Any]:
        return self.ingester.ingest(chapter, mode=mode)

    def consolidate(self) -> dict[str, int | str]:
        return self.consolidator.run()

    def get(
        self,
        ref: str | list[str],
        *,
        include: Literal["brief", "full", "body"] = "brief",
        as_of: int | None = None,
        response_format: ResponseFormat = "concise",
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> Any:
        self._response_format(response_format)
        if include not in {"brief", "full", "body"}:
            raise ValueError("include는 brief, full, body 중 하나여야 합니다")
        self._as_of(as_of)
        refs = [ref] if isinstance(ref, str) else ref
        if (
            not isinstance(refs, list)
            or not refs
            or not all(isinstance(item, str) for item in refs)
        ):
            raise ValueError("ref는 문자열 또는 비어 있지 않은 문자열 배열이어야 합니다")
        if include == "body" and len(refs) != 1:
            raise ValueError("include=body는 한 번에 노드 하나만 읽을 수 있습니다")
        ids = [self.addresses.resolve(item) for item in refs]
        result = self.store.get_nodes(ids, include=include, as_of=as_of)
        if len(result) != len(ids):
            raise ValueError("as_of 시점에 공개되지 않은 노드가 포함되어 있습니다")
        return fit_response(result, max_chars)

    def refs(
        self,
        ref: str,
        *,
        dir: Literal["in", "out", "both"] = "in",
        rel: list[str] | None = None,
        include_soft: bool = False,
        as_of: int | None = None,
        response_format: ResponseFormat = "concise",
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> Any:
        self._response_format(response_format)
        self._as_of(as_of)
        if dir not in {"in", "out", "both"}:
            raise ValueError("dir은 in, out, both 중 하나여야 합니다")
        relations = tuple(rel or ())
        unknown = sorted(set(relations) - set(self.ontology.edges))
        if unknown:
            raise OntologyError(f"알 수 없는 간선 타입: {', '.join(unknown)}")
        node_id = self.addresses.resolve(ref)
        result = self.store.refs(
            node_id,
            direction=dir,
            relations=relations,
            include_soft=include_soft,
            as_of=as_of,
        )
        return fit_response(result, max_chars)

    def _load_rules(self) -> dict[str, Any]:
        try:
            value = json.loads(self.rules_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"규칙 카탈로그를 읽을 수 없습니다: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("규칙 카탈로그 루트는 객체여야 합니다")
        return value

    def _kinds(self, kinds: list[str] | None) -> tuple[str, ...]:
        if kinds is None:
            return ()
        if not isinstance(kinds, list) or not all(isinstance(item, str) for item in kinds):
            raise ValueError("kind는 문자열 배열이어야 합니다")
        return tuple(self.ontology.canonical_kind(item) for item in kinds)

    @staticmethod
    def _tags(tags: list[str] | None) -> tuple[str, ...]:
        if tags is None:
            return ()
        if not isinstance(tags, list) or not all(isinstance(item, str) for item in tags):
            raise ValueError("tag는 문자열 배열이어야 합니다")
        return tuple(item if item.startswith("#") else f"#{item}" for item in tags)

    @staticmethod
    def _as_of(as_of: int | None) -> None:
        if as_of is not None and (
            not isinstance(as_of, int) or isinstance(as_of, bool) or as_of < 0
        ):
            raise ValueError("as_of는 0 이상의 정수여야 합니다")

    @staticmethod
    def _response_format(value: str) -> None:
        if value not in {"concise", "detailed"}:
            raise ValueError("response_format은 concise 또는 detailed여야 합니다")

    @staticmethod
    def _concise_schema(value: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for section, items in value.items():
            if section == "kinds":
                keys = ("name", "layer", "label", "p0", "internal", "props")
            elif section == "edges":
                keys = ("rel", "group", "hard", "src", "dst")
            elif section == "rules":
                keys = ("id", "tier", "category", "severity", "desc")
            else:
                keys = ("name", "schema")
            result[section] = [
                {key: item[key] for key in keys if key in item}
                for item in items
                if isinstance(item, dict)
            ]
        return result
