"""Deterministic P5 Domino planning with proposal-gated output."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .merkle import canonical_json, node_content
from .ontology import Ontology
from .ops import field_value, normalize_node_id


@dataclass(frozen=True, slots=True)
class Dependency:
    source: str
    target: str
    source_rev: int
    proposal: str


@dataclass(frozen=True, slots=True)
class CascadeCandidate:
    target: str
    ops: list[dict[str, Any]]
    read_set: list[dict[str, Any]]
    rationale: str


@dataclass(frozen=True, slots=True)
class CascadeJob:
    id: str
    node: str
    depth: int
    sources: list[str]
    target_field: str
    instruction: str
    original_rev: int
    target_rev: int
    source_revs: dict[str, int]
    max_tokens: int


@dataclass(slots=True)
class CascadePlan:
    run_id: str
    trigger_op: int | None
    status: str
    depth_reached: int
    nodes_visited: int
    cutoff_hits: int
    iteration: int = 1
    items: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[CascadeCandidate] = field(default_factory=list)
    jobs: list[CascadeJob] = field(default_factory=list)
    max_tokens: int = 0
    tokens_reserved: int = 0


class CascadeEngine:
    """Find dirty derived nodes without mutating the graph or calling an LLM."""

    _PROJECTION_FIELDS = (
        "kind",
        "aliases",
        "tags",
        "features",
        "props",
        "edges",
        "story_from",
        "story_to",
        "reveal_at",
        "locked",
        "visible_to",
    )
    _SOURCE_ROOTS = {
        "kind",
        "aliases",
        "tags",
        "features",
        "props",
        "story_from",
        "story_to",
        "reveal_at",
        "locked",
        "visible_to",
    }
    _TARGET_ROOTS = {
        "aliases",
        "tags",
        "features",
        "props",
        "story_from",
        "story_to",
        "reveal_at",
        "visible_to",
    }

    def __init__(self, rules_path: str | Path, ontology: Ontology) -> None:
        self.ontology = ontology
        try:
            rules = json.loads(Path(rules_path).resolve().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cascade 규칙을 읽을 수 없습니다: {rules_path}: {exc}") from exc
        config = rules.get("cascade")
        if not isinstance(config, dict):
            raise ValueError("rules.cascade 설정이 필요합니다")
        budgets = config.get("budgets", {})
        cycles = config.get("cycles", {})
        self.max_depth = self._positive_int(budgets, "max_depth")
        self.max_nodes = self._positive_int(budgets, "max_nodes")
        self.max_tokens = self._positive_int(budgets, "max_tokens")
        self.max_iterations = self._positive_int(cycles, "max_iterations")

    @staticmethod
    def _positive_int(config: dict[str, Any], key: str) -> int:
        value = config.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"cascade.{key}는 양의 정수여야 합니다")
        return value

    @staticmethod
    def index_proposal(
        connection: sqlite3.Connection,
        proposal_id: str,
        reads: list[Any],
    ) -> None:
        for entry in reads:
            node = entry.node if hasattr(entry, "node") else str(entry["node"])
            rev = entry.rev if hasattr(entry, "rev") else int(entry["rev"])
            connection.execute(
                "INSERT OR REPLACE INTO proposal_read(proposal, node, rev) VALUES (?, ?, ?)",
                (proposal_id, node, rev),
            )

    @classmethod
    def backfill(cls, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT p.id, p.read_set
            FROM proposal AS p
            WHERE NOT EXISTS (
              SELECT 1 FROM proposal_read AS pr WHERE pr.proposal = p.id
            )
            ORDER BY p.ts, p.id
            """
        ).fetchall()
        for row in rows:
            try:
                reads = json.loads(row["read_set"])
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(reads, list):
                cls.index_proposal(connection, str(row["id"]), reads)

    def plan(
        self,
        connection: sqlite3.Connection,
        *,
        trigger_proposal: str,
        trigger_nodes: set[str],
        forced_nodes: set[str],
        diagnostics: list[dict[str, Any]],
        allow_cycles: bool = False,
        max_iterations: int | None = None,
    ) -> CascadePlan:
        if max_iterations is None:
            max_iterations = self.max_iterations
        if not isinstance(max_iterations, int) or not 1 <= max_iterations <= self.max_iterations:
            raise ValueError(f"cascade max_iterations는 1..{self.max_iterations} 범위여야 합니다")
        trigger_row = connection.execute(
            "SELECT id FROM op WHERE proposal = ? ORDER BY seq LIMIT 1",
            (trigger_proposal,),
        ).fetchone()
        plan = CascadePlan(
            run_id=self._run_id(),
            trigger_op=int(trigger_row["id"]) if trigger_row is not None else None,
            status="done",
            depth_reached=0,
            nodes_visited=0,
            cutoff_hits=0,
            iteration=self._cascade_iteration(connection, trigger_proposal),
            max_tokens=self.max_tokens,
        )
        dependencies = self._dependencies(connection)
        adjacency: dict[str, list[Dependency]] = defaultdict(list)
        for dependency in dependencies:
            adjacency[dependency.source].append(dependency)

        queue: deque[tuple[str, int]] = deque((node, 0) for node in sorted(trigger_nodes))
        visited: set[tuple[str, int]] = set()
        dirty: set[str] = set()
        depths: dict[str, int] = {node: 0 for node in trigger_nodes}
        parents: dict[str, set[str]] = defaultdict(set)
        traversed_edges: set[tuple[str, str]] = set()
        item_by_node: dict[str, dict[str, Any]] = {}

        while queue:
            source, depth = queue.popleft()
            current_rev = self._current_rev(connection, source)
            state = (source, current_rev)
            if state in visited:
                continue
            visited.add(state)
            plan.nodes_visited = len(visited)
            plan.depth_reached = max(plan.depth_reached, depth)
            if plan.nodes_visited > self.max_nodes:
                plan.status = "budget_exceeded"
                break
            for dependency in adjacency.get(source, []):
                target = dependency.target
                next_depth = depth + 1
                if next_depth > self.max_depth:
                    plan.status = "budget_exceeded"
                    break
                if (
                    source in trigger_nodes
                    and source not in forced_nodes
                    and not self._typed_changed(connection, source, dependency.source_rev)
                ):
                    plan.cutoff_hits += 1
                    self._record_item(
                        item_by_node,
                        node=target,
                        depth=next_depth,
                        status="cutoff",
                        reason=f"typed_projection_unchanged:{source}",
                        diagnostics=diagnostics,
                    )
                    continue
                target_row = connection.execute(
                    "SELECT rev, locked FROM live_node WHERE id = ?", (target,)
                ).fetchone()
                if target_row is None:
                    plan.cutoff_hits += 1
                    self._record_item(
                        item_by_node,
                        node=target,
                        depth=next_depth,
                        status="cutoff",
                        reason="target_not_live",
                        diagnostics=diagnostics,
                    )
                    continue
                if bool(target_row["locked"]):
                    plan.cutoff_hits += 1
                    self._record_item(
                        item_by_node,
                        node=target,
                        depth=next_depth,
                        status="locked",
                        reason="locked_canon_skipped",
                        diagnostics=diagnostics,
                    )
                    continue
                traversed_edges.add((source, target))
                parents[target].add(source)
                depths[target] = min(depths.get(target, next_depth), next_depth)
                if target not in trigger_nodes:
                    dirty.add(target)
                queue.append((target, next_depth))
            if plan.status == "budget_exceeded":
                break

        cycle_nodes = self._cycle_nodes(trigger_nodes | dirty, traversed_edges)
        cycle_blocked = bool(cycle_nodes) and (not allow_cycles or plan.iteration > max_iterations)
        if cycle_blocked:
            plan.status = "cycle"
            cycle_reason = (
                f"bounded_iteration_limit:{max_iterations}" if allow_cycles else "dependency_cycle"
            )
            for node in sorted(cycle_nodes):
                self._record_item(
                    item_by_node,
                    node=node,
                    depth=depths.get(node, 0),
                    status="blocked",
                    reason=cycle_reason,
                    diagnostics=diagnostics,
                )
        if plan.status in {"cycle", "budget_exceeded"}:
            if plan.status == "budget_exceeded":
                for node in sorted(dirty):
                    self._record_item(
                        item_by_node,
                        node=node,
                        depth=depths[node],
                        status="blocked",
                        reason="cascade_budget_exceeded",
                        diagnostics=diagnostics,
                    )
            plan.items = self._ordered_items(item_by_node)
            return plan

        order = (
            self._topological_order(trigger_nodes | dirty, traversed_edges)
            if not cycle_nodes
            else sorted(dirty, key=lambda node: (depths.get(node, 0), node))
        )
        if cycle_nodes:
            # An opt-in bounded run visits each current (node, rev) state once. Since
            # output remains uncommitted proposals, later accepted proposals begin
            # the next bounded iteration rather than feeding output into itself.
            order = order[: self.max_nodes * max_iterations]
        for target in (node for node in order if node in dirty):
            upstream_dirty = parents[target] & dirty
            if upstream_dirty:
                self._record_item(
                    item_by_node,
                    node=target,
                    depth=depths[target],
                    status="dirty",
                    reason="awaiting_upstream:" + ",".join(sorted(upstream_dirty)),
                    diagnostics=diagnostics,
                )
                continue
            candidate, reason = self._candidate(
                connection,
                trigger_proposal=trigger_proposal,
                target=target,
                sources=parents[target],
            )
            jobs, job_reason = self._llm_jobs(
                connection,
                plan=plan,
                target=target,
                depth=depths[target],
                sources=parents[target],
            )
            if plan.tokens_reserved + sum(job.max_tokens for job in jobs) > self.max_tokens:
                plan.status = "budget_exceeded"
                break
            plan.tokens_reserved += sum(job.max_tokens for job in jobs)
            plan.jobs.extend(jobs)
            if candidate is None and not jobs:
                if reason == "derived_values_unchanged":
                    plan.cutoff_hits += 1
                self._record_item(
                    item_by_node,
                    node=target,
                    depth=depths[target],
                    status="cutoff" if reason == "derived_values_unchanged" else "dirty",
                    reason=job_reason or reason,
                    diagnostics=diagnostics,
                )
                continue
            if candidate is not None:
                plan.candidates.append(candidate)
            self._record_item(
                item_by_node,
                node=target,
                depth=depths[target],
                status="dirty" if candidate is not None else "queued",
                reason=(
                    "proposal_and_tier2_ready"
                    if candidate is not None and jobs
                    else "proposal_ready"
                    if candidate is not None
                    else "tier2_queued"
                ),
                diagnostics=diagnostics,
            )
        if plan.status == "budget_exceeded":
            plan.candidates.clear()
            plan.jobs.clear()
            for node in sorted(dirty):
                self._record_item(
                    item_by_node,
                    node=node,
                    depth=depths[node],
                    status="blocked",
                    reason="cascade_token_budget_exceeded",
                    diagnostics=diagnostics,
                )
            plan.items = self._ordered_items(item_by_node, order=order)
            return plan
        plan.items = self._ordered_items(item_by_node, order=order)
        return plan

    def _dependencies(self, connection: sqlite3.Connection) -> list[Dependency]:
        rows = connection.execute(
            """
            SELECT pr.node AS source, pr.rev AS source_rev,
                   o.target, p.id AS proposal, p.ts, o.seq
            FROM proposal_read AS pr
            JOIN proposal AS p ON p.id = pr.proposal AND p.status = 'accepted'
            JOIN op AS o ON o.proposal = p.id
            WHERE pr.node <> 'book' AND pr.node <> o.target
            ORDER BY p.ts DESC, p.id DESC, o.seq DESC
            """
        ).fetchall()
        latest: dict[tuple[str, str], Dependency] = {}
        for row in rows:
            key = (str(row["source"]), str(row["target"]))
            latest.setdefault(
                key,
                Dependency(
                    source=key[0],
                    target=key[1],
                    source_rev=int(row["source_rev"]),
                    proposal=str(row["proposal"]),
                ),
            )
        return sorted(latest.values(), key=lambda item: (item.source, item.target))

    @staticmethod
    def _cascade_iteration(connection: sqlite3.Connection, proposal_id: str) -> int:
        iteration = 1
        current = proposal_id
        seen: set[str] = set()
        while current not in seen:
            seen.add(current)
            row = connection.execute(
                """
                SELECT parent_op.proposal AS parent
                FROM cascade_item AS item
                JOIN cascade_run AS run ON run.id = item.run
                JOIN op AS parent_op ON parent_op.id = run.trigger_op
                WHERE item.proposal = ?
                ORDER BY run.ts DESC, run.id DESC
                LIMIT 1
                """,
                (current,),
            ).fetchone()
            if row is None or not row["parent"]:
                break
            iteration += 1
            current = str(row["parent"])
        return iteration

    def _candidate(
        self,
        connection: sqlite3.Connection,
        *,
        trigger_proposal: str,
        target: str,
        sources: set[str],
    ) -> tuple[CascadeCandidate | None, str]:
        target_row = connection.execute(
            "SELECT rev, props FROM live_node WHERE id = ?", (target,)
        ).fetchone()
        if target_row is None:
            return None, "target_not_live"
        try:
            props = json.loads(target_row["props"] or "{}")
        except json.JSONDecodeError:
            return None, "invalid_target_props"
        contracts = props.get("_derive")
        if not isinstance(contracts, list) or not contracts:
            return None, "no_deterministic_contract"

        target_content = node_content(connection, target)
        operations: list[dict[str, Any]] = []
        read_nodes = {target}
        values_by_field: dict[str, str] = {}
        for contract in contracts:
            if not isinstance(contract, dict) or contract.get("transform", "copy") != "copy":
                continue
            raw_source = contract.get("source")
            source_field = contract.get("source_field")
            target_field = contract.get("target_field")
            if not all(
                isinstance(value, str) and value
                for value in (
                    raw_source,
                    source_field,
                    target_field,
                )
            ):
                continue
            try:
                source = normalize_node_id(raw_source, self.ontology)
            except ValueError:
                continue
            if source not in sources or not self._safe_field(source_field, target=False):
                continue
            if not self._safe_field(target_field, target=True):
                continue
            source_row = connection.execute(
                "SELECT rev FROM live_node WHERE id = ?", (source,)
            ).fetchone()
            if source_row is None:
                continue
            source_value = field_value(node_content(connection, source), source_field)
            current_value = field_value(target_content, target_field)
            if source_value == current_value:
                read_nodes.add(source)
                continue
            serialized = canonical_json(source_value)
            previous = values_by_field.get(target_field)
            if previous is not None and previous != serialized:
                return None, f"conflicting_contracts:{target_field}"
            if previous is not None:
                read_nodes.add(source)
                continue
            values_by_field[target_field] = serialized
            read_nodes.add(source)
            identity = canonical_json(
                {
                    "trigger": trigger_proposal,
                    "source": source,
                    "source_rev": int(source_row["rev"]),
                    "target": target,
                    "target_rev": int(target_row["rev"]),
                    "field": target_field,
                    "to": source_value,
                }
            )
            operations.append(
                {
                    "verb": "UPDATE",
                    "target": target,
                    "field": target_field,
                    "from": current_value,
                    "to": source_value,
                    "basis_rev": int(target_row["rev"]),
                    "idem_key": "cascade-" + hashlib.sha256(identity.encode()).hexdigest(),
                }
            )
        if not operations:
            return None, "derived_values_unchanged"
        operations.sort(key=lambda item: (str(item["field"]), str(item["idem_key"])))
        read_set = []
        for node in sorted(read_nodes):
            row = connection.execute("SELECT rev FROM live_node WHERE id = ?", (node,)).fetchone()
            if row is None:
                return None, f"read_source_not_live:{node}"
            read_set.append({"node": node, "rev": int(row["rev"])})
        return (
            CascadeCandidate(
                target=target,
                ops=operations,
                read_set=read_set,
                rationale=f"Domino v1 deterministic rederive after {trigger_proposal}",
            ),
            "proposal_ready",
        )

    def _llm_jobs(
        self,
        connection: sqlite3.Connection,
        *,
        plan: CascadePlan,
        target: str,
        depth: int,
        sources: set[str],
    ) -> tuple[list[CascadeJob], str | None]:
        if depth > 2:
            return [], "tier2_max_hops_exceeded"
        target_row = connection.execute(
            "SELECT rev, props FROM live_node WHERE id = ?", (target,)
        ).fetchone()
        if target_row is None:
            return [], "target_not_live"
        try:
            contracts = json.loads(target_row["props"] or "{}").get("_rederive")
        except json.JSONDecodeError:
            return [], "invalid_target_props"
        if not isinstance(contracts, list) or not contracts:
            return [], None
        original_rev = self._latest_human_revision(connection, target)
        if original_rev is None:
            return [], "tier2_requires_human_origin"

        jobs: list[CascadeJob] = []
        seen_fields: set[str] = set()
        invalid = 0
        for contract in contracts:
            if not isinstance(contract, dict):
                invalid += 1
                continue
            raw_sources = contract.get("sources")
            if raw_sources is None and contract.get("source") is not None:
                raw_sources = [contract["source"]]
            target_field = contract.get("target_field")
            instruction = contract.get("instruction")
            max_tokens = contract.get("max_tokens", 1200)
            if (
                not isinstance(raw_sources, list)
                or not raw_sources
                or not isinstance(target_field, str)
                or not self._safe_llm_target(target_field)
                or not isinstance(instruction, str)
                or not instruction.strip()
                or len(instruction) > 2000
                or not isinstance(max_tokens, int)
                or isinstance(max_tokens, bool)
                or not 1 <= max_tokens <= self.max_tokens
                or target_field in seen_fields
            ):
                invalid += 1
                continue
            normalized_sources: list[str] = []
            try:
                for raw_source in raw_sources:
                    if not isinstance(raw_source, str):
                        raise ValueError("source must be a string")
                    normalized_sources.append(normalize_node_id(raw_source, self.ontology))
            except ValueError:
                invalid += 1
                continue
            normalized_sources = sorted(set(normalized_sources))
            if not normalized_sources or not set(normalized_sources).issubset(sources):
                invalid += 1
                continue
            source_revs: dict[str, int] = {}
            missing = False
            for source in normalized_sources:
                row = connection.execute(
                    "SELECT rev FROM live_node WHERE id = ?", (source,)
                ).fetchone()
                if row is None:
                    missing = True
                    break
                source_revs[source] = int(row["rev"])
            if missing:
                invalid += 1
                continue
            seen_fields.add(target_field)
            identity = canonical_json(
                {
                    "run": plan.run_id,
                    "node": target,
                    "field": target_field,
                    "sources": source_revs,
                }
            )
            jobs.append(
                CascadeJob(
                    id="job/" + hashlib.sha256(identity.encode()).hexdigest(),
                    node=target,
                    depth=depth,
                    sources=normalized_sources,
                    target_field=target_field,
                    instruction=instruction.strip(),
                    original_rev=original_rev,
                    target_rev=int(target_row["rev"]),
                    source_revs=source_revs,
                    max_tokens=max_tokens,
                )
            )
        if jobs:
            return jobs, None
        return [], "invalid_tier2_contract" if invalid else None

    @staticmethod
    def _latest_human_revision(
        connection: sqlite3.Connection,
        node: str,
    ) -> int | None:
        rows = connection.execute(
            "SELECT rev, snapshot FROM node_revision WHERE node = ? ORDER BY rev DESC",
            (node,),
        ).fetchall()
        for row in rows:
            try:
                if json.loads(row["snapshot"]).get("origin") == "human":
                    return int(row["rev"])
            except (TypeError, json.JSONDecodeError):
                continue
        return None

    @classmethod
    def _safe_llm_target(cls, field: str) -> bool:
        if field in {"title", "summary", "body"}:
            return True
        return cls._safe_field(field, target=True)

    @classmethod
    def _safe_field(cls, field: str, *, target: bool) -> bool:
        root = field.split(".", 1)[0]
        allowed = cls._TARGET_ROOTS if target else cls._SOURCE_ROOTS
        if root not in allowed:
            return False
        if target and root == "props" and "." not in field:
            return False
        if target and root != "props" and "." in field:
            return False
        return field != "props._derive" and not field.startswith("props._derive.")

    @classmethod
    def _typed_changed(
        cls,
        connection: sqlite3.Connection,
        node: str,
        historical_rev: int,
    ) -> bool:
        old_row = connection.execute(
            "SELECT snapshot FROM node_revision WHERE node = ? AND rev = ?",
            (node, historical_rev),
        ).fetchone()
        current_row = connection.execute("SELECT tx_to FROM node WHERE id = ?", (node,)).fetchone()
        if old_row is None or current_row is None or current_row["tx_to"] is not None:
            return True
        try:
            snapshot = json.loads(old_row["snapshot"])
            old_content = snapshot["content"]
        except (TypeError, KeyError, json.JSONDecodeError):
            return True
        current = node_content(connection, node)
        old_projection = {key: old_content.get(key) for key in cls._PROJECTION_FIELDS}
        current_projection = {key: current.get(key) for key in cls._PROJECTION_FIELDS}
        return canonical_json(old_projection) != canonical_json(current_projection)

    @staticmethod
    def _current_rev(connection: sqlite3.Connection, node: str) -> int:
        row = connection.execute("SELECT rev FROM node WHERE id = ?", (node,)).fetchone()
        return int(row["rev"]) if row is not None else -1

    @staticmethod
    def _cycle_nodes(nodes: set[str], edges: set[tuple[str, str]]) -> set[str]:
        adjacency: dict[str, set[str]] = defaultdict(set)
        reverse: dict[str, set[str]] = defaultdict(set)
        for source, target in edges:
            if source in nodes and target in nodes:
                adjacency[source].add(target)
                reverse[target].add(source)

        def reachable(start: str, graph: dict[str, set[str]]) -> set[str]:
            seen: set[str] = set()
            stack = [start]
            while stack:
                current = stack.pop()
                for neighbor in graph.get(current, set()):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        stack.append(neighbor)
            return seen

        return {
            node
            for node in nodes
            if node in reachable(node, adjacency) and node in reachable(node, reverse)
        }

    @staticmethod
    def _topological_order(nodes: set[str], edges: set[tuple[str, str]]) -> list[str]:
        adjacency: dict[str, set[str]] = defaultdict(set)
        indegree = {node: 0 for node in nodes}
        for source, target in edges:
            if source in nodes and target in nodes and target not in adjacency[source]:
                adjacency[source].add(target)
                indegree[target] += 1
        ready = sorted(node for node, count in indegree.items() if count == 0)
        result: list[str] = []
        while ready:
            node = ready.pop(0)
            result.append(node)
            for target in sorted(adjacency.get(node, set())):
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
                    ready.sort()
        return result

    @staticmethod
    def _record_item(
        items: dict[str, dict[str, Any]],
        *,
        node: str,
        depth: int,
        status: str,
        reason: str,
        diagnostics: list[dict[str, Any]],
    ) -> None:
        rank = {"cutoff": 0, "locked": 1, "queued": 2, "dirty": 2, "blocked": 3}
        existing = items.get(node)
        if existing is not None and rank.get(existing["status"], 0) > rank.get(status, 0):
            return
        relevant = [
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.get("node") == node or node in diagnostic.get("related", [])
        ]
        items[node] = {
            "node": node,
            "depth": depth,
            "status": status,
            "reason": reason,
            "proposal_id": None,
            "diagnostics": relevant,
        }

    @staticmethod
    def _ordered_items(
        items: dict[str, dict[str, Any]],
        *,
        order: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        positions = {node: index for index, node in enumerate(order or [])}
        fallback = len(positions)
        return sorted(
            items.values(),
            key=lambda item: (
                positions.get(str(item["node"]), fallback),
                int(item["depth"]),
                str(item["node"]),
            ),
        )

    @staticmethod
    def _run_id() -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return f"cascade/{stamp}-{uuid.uuid4().hex[:10]}"
