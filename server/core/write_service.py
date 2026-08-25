"""P1 proposal lifecycle, conflict detection, and serialized commits."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .branch import BranchService
from .cascade import CascadeCandidate, CascadeEngine, CascadePlan
from .database import connect_write
from .diagnostics import DiagnosticEngine
from .merkle import advance_graph_state, canonical_json, ensure_graph_state
from .models import Operation, ProposalInput, ReadSetEntry
from .ontology import Ontology
from .ops import Actor, OperationApplier, field_value, normalize_node_id
from .policy import RiskPolicy

_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _database_lock(path: Path) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path, threading.RLock())


class ProposalError(ValueError):
    pass


class WriteService:
    def __init__(
        self,
        *,
        db_path: str | Path,
        ontology: Ontology,
        policy_path: str | Path,
        rules_path: str | Path,
    ) -> None:
        self.db_path = Path(db_path).resolve()
        self.ontology = ontology
        self.policy = RiskPolicy(policy_path)
        self.diagnostics = DiagnosticEngine(self.db_path, rules_path)
        self.applier = OperationApplier(ontology)
        self.cascade = CascadeEngine(rules_path, ontology)
        self._lock = _database_lock(self.db_path)
        with self._lock, connect_write(self.db_path) as connection:
            ensure_graph_state(connection)
            self.cascade.backfill(connection)
            BranchService.backfill(connection, datetime.now(UTC).isoformat())

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
        parent_session_id: str | None = None,
    ) -> dict[str, Any]:
        request = ProposalInput.model_validate(
            {
                "ops": ops,
                "read_set": read_set,
                "rationale": rationale,
                "session_id": session_id,
                "actor_kind": actor_kind,
                "model_id": model_id,
                "host": host,
                "on_behalf_of": on_behalf_of,
                "parent_session_id": parent_session_id,
            }
        )
        operations = [self.applier.normalize(operation) for operation in request.ops]
        normalized_reads = self._normalize_read_set(request.read_set)
        request = request.model_copy(update={"ops": operations, "read_set": normalized_reads})
        with self._lock, connect_write(self.db_path) as connection:
            return self._store_proposal(
                connection,
                request=request,
                operations=operations,
                now=datetime.now(UTC).isoformat(),
            )

    def commit(
        self,
        proposal_id: str,
        *,
        mode: Literal["apply", "dry_run"] = "apply",
        allow_cycles: bool = False,
        max_iterations: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise ProposalError("proposal_id는 비어 있지 않은 문자열이어야 합니다")
        if mode not in {"apply", "dry_run"}:
            raise ProposalError("mode는 apply 또는 dry_run이어야 합니다")
        if not isinstance(allow_cycles, bool):
            raise ProposalError("allow_cycles는 bool이어야 합니다")
        with self._lock, connect_write(self.db_path) as connection:
            existing = connection.execute(
                "SELECT result FROM commit_record WHERE proposal = ?", (proposal_id,)
            ).fetchone()
            if existing is not None:
                result = json.loads(existing["result"])
                if "branch" not in result:
                    stored = connection.execute(
                        "SELECT session_id FROM proposal WHERE id = ?", (proposal_id,)
                    ).fetchone()
                    if stored is not None and stored["session_id"]:
                        result["branch"] = BranchService.get(connection, str(stored["session_id"]))
                return result
            proposal = connection.execute(
                "SELECT * FROM proposal WHERE id = ?", (proposal_id,)
            ).fetchone()
            if proposal is None:
                raise ProposalError(f"제안을 찾을 수 없습니다: {proposal_id}")
            if proposal["status"] != "open":
                raise ProposalError(f"커밋할 수 없는 제안 상태입니다: {proposal['status']}")
            operations = self._load_operations(connection, proposal_id)
            reads = [ReadSetEntry.model_validate(item) for item in json.loads(proposal["read_set"])]
            conflicts = self._detect_conflicts(connection, operations, reads)
            if conflicts:
                graph = connection.execute(
                    "SELECT revision, root_cid FROM graph_state WHERE singleton = 1"
                ).fetchone()
                result = {
                    "proposal_id": proposal_id,
                    "status": "rejected",
                    "applied": [],
                    "rejected": conflicts,
                    "graph_revision": int(graph["revision"]),
                    "root_cid": str(graph["root_cid"]),
                    "diagnostics": [],
                    "cascade_id": None,
                    "branch": BranchService.get(connection, str(proposal["session_id"])),
                }
                if mode == "apply":
                    result["branch"] = BranchService.conflicted(
                        connection,
                        str(proposal["session_id"]),
                        datetime.now(UTC).isoformat(),
                    )
                    connection.execute(
                        "UPDATE proposal SET status = 'rejected' WHERE id = ?", (proposal_id,)
                    )
                    now = datetime.now(UTC).isoformat()
                    connection.execute(
                        """
                        INSERT INTO commit_record(
                          proposal, graph_revision, root_cid, result, committed_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            proposal_id,
                            graph["revision"],
                            graph["root_cid"],
                            canonical_json(result),
                            now,
                        ),
                    )
                return result
            actor = Actor(
                kind=proposal["actor_kind"],
                model_id=proposal["model_id"],
                host=proposal["host"] or "codex",
                on_behalf_of=self._on_behalf_of(connection, proposal_id),
            )
            now = datetime.now(UTC).isoformat()
            if mode == "dry_run":
                connection.execute("SAVEPOINT storyai_dry_run")
            try:
                applied: list[dict[str, Any]] = []
                for operation in operations:
                    applied.extend(
                        self.applier.apply(
                            connection,
                            operation,
                            proposal_id=proposal_id,
                            actor=actor,
                            now=now,
                        )
                    )
                diagnostics = self.diagnostics.evaluate(connection)
                self.diagnostics.synchronize(connection, diagnostics, now)
                connection.execute(
                    "UPDATE proposal SET status = 'accepted' WHERE id = ?", (proposal_id,)
                )
                trigger_nodes = {
                    str(item["target"])
                    for item in applied
                    if isinstance(item, dict) and item.get("target")
                }
                forced_nodes = {
                    operation.target for operation in operations if operation.verb == "INVALIDATE"
                }
                forced_nodes.update(
                    str(operation.to_value)
                    for operation in operations
                    if operation.verb in {"LINK", "UNLINK"}
                )
                trigger_nodes.update(forced_nodes)
                cascade = self.cascade.plan(
                    connection,
                    trigger_proposal=proposal_id,
                    trigger_nodes=trigger_nodes,
                    forced_nodes=forced_nodes,
                    diagnostics=diagnostics,
                    allow_cycles=allow_cycles,
                    max_iterations=max_iterations,
                )
                self._materialize_cascade(
                    connection,
                    plan=cascade,
                    trigger_proposal=proposal_id,
                    now=now,
                )
                self._persist_cascade(connection, cascade, now)
                graph_revision, root_cid = advance_graph_state(connection, now)
                branch = BranchService.accepted(
                    connection,
                    str(proposal["session_id"]),
                    graph_revision,
                    now,
                )
                result = {
                    "proposal_id": proposal_id,
                    "status": "dry_run" if mode == "dry_run" else "accepted",
                    "applied": applied,
                    "rejected": [],
                    "graph_revision": graph_revision,
                    "root_cid": root_cid,
                    "diagnostics": diagnostics,
                    "cascade_id": cascade.run_id,
                    "cascade": self._cascade_result(cascade),
                    "branch": branch,
                }
                if mode == "dry_run":
                    connection.execute("ROLLBACK TO storyai_dry_run")
                    connection.execute("RELEASE storyai_dry_run")
                    return result
                connection.execute(
                    """
                    INSERT INTO commit_record(
                      proposal, graph_revision, root_cid, result, committed_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (proposal_id, graph_revision, root_cid, canonical_json(result), now),
                )
                return result
            except Exception:
                if mode == "dry_run":
                    connection.execute("ROLLBACK TO storyai_dry_run")
                    connection.execute("RELEASE storyai_dry_run")
                raise

    def graph_revision(self) -> dict[str, Any]:
        with connect_write(self.db_path) as connection:
            row = connection.execute(
                "SELECT revision, root_cid, updated_at FROM graph_state WHERE singleton = 1"
            ).fetchone()
        return dict(row) if row is not None else {}

    def _store_proposal(
        self,
        connection: sqlite3.Connection,
        *,
        request: ProposalInput,
        operations: list[Operation],
        now: str,
    ) -> dict[str, Any]:
        duplicate = self._idempotent_result(connection, operations)
        if duplicate is not None:
            return duplicate
        reads = list(request.read_set)
        branch = BranchService.ensure(
            connection,
            session_id=request.session_id,
            parent=request.parent_session_id,
            now=now,
        )
        self._require_read_coverage(operations, reads)
        conflicts = self._detect_conflicts(connection, operations, reads)
        pending = self._pending_overlap(connection, operations)
        decision = self.policy.assess(connection, operations, self.ontology)
        if decision.forbidden:
            raise ProposalError("; ".join(decision.forbidden))
        proposal_id = self._proposal_id()
        connection.execute(
            """
            INSERT INTO proposal(
              id, actor_kind, model_id, session_id, host, rationale, read_set, status, ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                proposal_id,
                request.actor_kind,
                request.model_id,
                request.session_id,
                request.host,
                request.rationale,
                canonical_json([item.model_dump(mode="json") for item in reads]),
                now,
            ),
        )
        self.cascade.index_proposal(connection, proposal_id, reads)
        for index, operation in enumerate(operations):
            connection.execute(
                """
                INSERT INTO op(
                  proposal, seq, verb, target, field, from_val, to_val,
                  basis_rev, idem_key, ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    index,
                    operation.verb,
                    operation.target,
                    operation.field,
                    self._wire_value(operation, "from_value"),
                    self._wire_value(operation, "to_value"),
                    operation.basis_rev,
                    operation.idem_key,
                    now,
                ),
            )
        connection.execute(
            """
            INSERT INTO proposal_assessment(
              proposal, risk, reasons, conflicts, pending_overlap
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                proposal_id,
                decision.risk,
                canonical_json(decision.reasons),
                canonical_json(conflicts),
                canonical_json(pending),
            ),
        )
        connection.execute(
            "INSERT INTO proposal_actor(proposal, on_behalf_of) VALUES (?, ?)",
            (proposal_id, request.on_behalf_of),
        )
        if not conflicts:
            self._simulate(connection, proposal_id, request, operations, now)
        else:
            branch = BranchService.conflicted(connection, request.session_id, now)
        return {
            "proposal_id": proposal_id,
            "status": "conflicted" if conflicts else "open",
            "risk": decision.risk,
            "reasons": list(decision.reasons),
            "conflicts": conflicts,
            "pending_overlap": pending,
            "branch": branch,
        }

    def _materialize_cascade(
        self,
        connection: sqlite3.Connection,
        *,
        plan: CascadePlan,
        trigger_proposal: str,
        now: str,
    ) -> None:
        for index, candidate in enumerate(plan.candidates):
            savepoint = f"storyai_cascade_{index}"
            connection.execute(f"SAVEPOINT {savepoint}")
            try:
                result = self._store_cascade_candidate(
                    connection,
                    candidate=candidate,
                    trigger_proposal=trigger_proposal,
                    now=now,
                )
            except (ValueError, sqlite3.IntegrityError) as exc:
                connection.execute(f"ROLLBACK TO {savepoint}")
                connection.execute(f"RELEASE {savepoint}")
                item = self._cascade_item(plan, candidate.target)
                item["status"] = "blocked"
                item["reason"] = f"proposal_failed:{type(exc).__name__}:{exc}"
                continue
            connection.execute(f"RELEASE {savepoint}")
            item = self._cascade_item(plan, candidate.target)
            item["status"] = "proposed"
            item["reason"] = f"candidate_{result['status']}"
            item["proposal_id"] = result["proposal_id"]

    def _store_cascade_candidate(
        self,
        connection: sqlite3.Connection,
        *,
        candidate: CascadeCandidate,
        trigger_proposal: str,
        now: str,
    ) -> dict[str, Any]:
        trigger = connection.execute(
            "SELECT session_id FROM proposal WHERE id = ?", (trigger_proposal,)
        ).fetchone()
        if trigger is None or not trigger["session_id"]:
            raise ProposalError(f"cascade trigger session을 찾을 수 없습니다: {trigger_proposal}")
        request = ProposalInput.model_validate(
            {
                "ops": candidate.ops,
                "read_set": candidate.read_set,
                "rationale": candidate.rationale,
                "session_id": f"cascade:{trigger_proposal}",
                "actor_kind": "cascade",
                "host": "codex",
                "on_behalf_of": self._on_behalf_of(connection, trigger_proposal),
                "parent_session_id": str(trigger["session_id"]),
            }
        )
        operations = [self.applier.normalize(operation) for operation in request.ops]
        reads = self._normalize_read_set(request.read_set)
        request = request.model_copy(update={"ops": operations, "read_set": reads})
        return self._store_proposal(
            connection,
            request=request,
            operations=operations,
            now=now,
        )

    @staticmethod
    def _cascade_item(plan: CascadePlan, target: str) -> dict[str, Any]:
        for item in plan.items:
            if item["node"] == target:
                return item
        raise RuntimeError(f"cascade item이 없습니다: {target}")

    @staticmethod
    def _persist_cascade(
        connection: sqlite3.Connection,
        plan: CascadePlan,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO cascade_run(
              id, trigger_op, depth_reached, nodes_visited, cutoff_hits, status, ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.run_id,
                plan.trigger_op,
                plan.depth_reached,
                plan.nodes_visited,
                plan.cutoff_hits,
                plan.status,
                now,
            ),
        )
        for index, item in enumerate(plan.items):
            connection.execute(
                """
                INSERT INTO cascade_item(
                  run, seq, node, depth, status, reason, proposal, diagnostics
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.run_id,
                    index,
                    item["node"],
                    item["depth"],
                    item["status"],
                    item["reason"],
                    item["proposal_id"],
                    canonical_json(item["diagnostics"]),
                ),
            )
        for job in plan.jobs:
            connection.execute(
                """
                INSERT INTO cascade_job(
                  id, run, node, depth, sources, target_field, instruction,
                  original_rev, target_rev, source_revs, max_tokens, status, attempts,
                  lease_until, claim_token, proposal, error, ts, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0,
                          NULL, NULL, NULL, NULL, ?, ?)
                """,
                (
                    job.id,
                    plan.run_id,
                    job.node,
                    job.depth,
                    canonical_json(job.sources),
                    job.target_field,
                    job.instruction,
                    job.original_rev,
                    job.target_rev,
                    canonical_json(job.source_revs),
                    job.max_tokens,
                    now,
                    now,
                ),
            )

    @staticmethod
    def _cascade_result(plan: CascadePlan) -> dict[str, Any]:
        return {
            "status": plan.status,
            "depth_reached": plan.depth_reached,
            "nodes_visited": plan.nodes_visited,
            "cutoff_hits": plan.cutoff_hits,
            "iteration": plan.iteration,
            "max_tokens": plan.max_tokens,
            "tokens_reserved": plan.tokens_reserved,
            "proposals": [
                item["proposal_id"] for item in plan.items if item["proposal_id"] is not None
            ],
            "jobs": [job.id for job in plan.jobs],
            "items": plan.items,
        }

    def _simulate(
        self,
        connection: sqlite3.Connection,
        proposal_id: str,
        request: ProposalInput,
        operations: list[Operation],
        now: str,
    ) -> None:
        actor = Actor(
            kind=request.actor_kind,
            model_id=request.model_id,
            host=request.host,
            on_behalf_of=request.on_behalf_of,
        )
        connection.execute("SAVEPOINT storyai_validate")
        try:
            for operation in operations:
                self.applier.apply(
                    connection,
                    operation,
                    proposal_id=proposal_id,
                    actor=actor,
                    now=now,
                )
        except Exception:
            connection.execute("ROLLBACK TO storyai_validate")
            connection.execute("RELEASE storyai_validate")
            raise
        connection.execute("ROLLBACK TO storyai_validate")
        connection.execute("RELEASE storyai_validate")

    def _detect_conflicts(
        self,
        connection: sqlite3.Connection,
        operations: list[Operation],
        reads: list[ReadSetEntry],
    ) -> list[dict[str, Any]]:
        conflicts: list[dict[str, Any]] = []
        by_target: dict[str, list[Operation]] = {}
        for operation in operations:
            by_target.setdefault(operation.target, []).append(operation)
            if operation.verb == "UPDATE" and "from_value" in operation.model_fields_set:
                current = self.applier.current_field(
                    connection, operation.target, operation.field or ""
                )
                if current != operation.from_value:
                    conflicts.append(
                        {
                            "node": operation.target,
                            "field": operation.field,
                            "reason": "from_mismatch",
                            "expected": operation.from_value,
                            "current": current,
                        }
                    )
        graph = connection.execute(
            "SELECT revision FROM graph_state WHERE singleton = 1"
        ).fetchone()
        graph_rev = int(graph["revision"]) if graph else 0
        for entry in reads:
            if entry.node == "book":
                if entry.rev != graph_rev:
                    conflicts.append(
                        {
                            "node": "book",
                            "reason": "revision_mismatch",
                            "expected_rev": entry.rev,
                            "current_rev": graph_rev,
                        }
                    )
                continue
            current = connection.execute(
                "SELECT rev FROM live_node WHERE id = ?", (entry.node,)
            ).fetchone()
            if current is None:
                conflicts.append({"node": entry.node, "reason": "missing_or_invalidated"})
                continue
            current_rev = int(current["rev"])
            if current_rev == entry.rev:
                continue
            target_ops = by_target.get(entry.node, [])
            if target_ops and all(op.verb == "UPDATE" for op in target_ops):
                historical = self._revision_content(connection, entry.node, entry.rev)
                if historical is not None and all(
                    field_value(historical, op.field or "")
                    == self.applier.current_field(connection, entry.node, op.field or "")
                    for op in target_ops
                ):
                    continue
            conflicts.append(
                {
                    "node": entry.node,
                    "reason": "revision_mismatch",
                    "expected_rev": entry.rev,
                    "current_rev": current_rev,
                }
            )
        return self._dedupe_dicts(conflicts)

    def _require_read_coverage(
        self, operations: list[Operation], reads: list[ReadSetEntry]
    ) -> None:
        covered = {entry.node for entry in reads}
        added = {operation.target for operation in operations if operation.verb == "ADD"}
        missing: set[str] = set()
        for operation in operations:
            if (
                operation.verb != "ADD"
                and operation.target not in covered
                and operation.target not in added
            ):
                missing.add(operation.target)
            if (
                operation.verb in {"LINK", "UNLINK"}
                and str(operation.to_value) not in covered
                and str(operation.to_value) not in added
            ):
                missing.add(str(operation.to_value))
            if operation.basis_rev is not None:
                matching = next((item for item in reads if item.node == operation.target), None)
                if matching is None or matching.rev != operation.basis_rev:
                    raise ProposalError(
                        f"basis_rev가 read_set과 일치하지 않습니다: {operation.target}"
                    )
        if missing:
            raise ProposalError(f"read_set에 쓰기 근거가 없습니다: {', '.join(sorted(missing))}")

    def _pending_overlap(
        self, connection: sqlite3.Connection, operations: list[Operation]
    ) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = []
        for operation in operations:
            rows = connection.execute(
                """
                SELECT o.proposal, o.field, o.verb
                FROM op AS o JOIN proposal AS p ON p.id = o.proposal
                WHERE p.status = 'open' AND o.target = ?
                ORDER BY o.proposal, o.seq
                """,
                (operation.target,),
            ).fetchall()
            for row in rows:
                if (
                    operation.verb != "UPDATE"
                    or row["verb"] != "UPDATE"
                    or operation.field == row["field"]
                ):
                    pending.append(
                        {
                            "proposal_id": row["proposal"],
                            "node": operation.target,
                            "field": row["field"],
                        }
                    )
        return self._dedupe_dicts(pending)

    def _idempotent_result(
        self, connection: sqlite3.Connection, operations: list[Operation]
    ) -> dict[str, Any] | None:
        placeholders = ",".join("?" for _ in operations)
        rows = connection.execute(
            f"SELECT * FROM op WHERE idem_key IN ({placeholders}) ORDER BY seq",
            [operation.idem_key for operation in operations],
        ).fetchall()
        if not rows:
            return None
        if len(rows) != len(operations) or len({row["proposal"] for row in rows}) != 1:
            raise ProposalError("idem_key 일부가 다른 제안에서 이미 사용되었습니다")
        by_key = {row["idem_key"]: row for row in rows}
        for operation in operations:
            row = by_key[operation.idem_key]
            expected = (
                operation.verb,
                operation.target,
                operation.field,
                self._wire_value(operation, "from_value"),
                self._wire_value(operation, "to_value"),
                operation.basis_rev,
            )
            actual = (
                row["verb"],
                row["target"],
                row["field"],
                row["from_val"],
                row["to_val"],
                row["basis_rev"],
            )
            if expected != actual:
                raise ProposalError(
                    f"idem_key가 다른 연산에 재사용되었습니다: {operation.idem_key}"
                )
        return self._proposal_result(connection, rows[0]["proposal"], idempotent=True)

    def _proposal_result(
        self, connection: sqlite3.Connection, proposal_id: str, *, idempotent: bool
    ) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT p.status, p.session_id, a.risk, a.reasons,
                   a.conflicts, a.pending_overlap
            FROM proposal AS p JOIN proposal_assessment AS a ON a.proposal = p.id
            WHERE p.id = ?
            """,
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise ProposalError(f"제안 판정을 찾을 수 없습니다: {proposal_id}")
        conflicts = json.loads(row["conflicts"])
        return {
            "proposal_id": proposal_id,
            "status": "conflicted" if conflicts and row["status"] == "open" else row["status"],
            "risk": row["risk"],
            "reasons": json.loads(row["reasons"]),
            "conflicts": conflicts,
            "pending_overlap": json.loads(row["pending_overlap"]),
            "idempotent": idempotent,
            "branch": BranchService.get(connection, str(row["session_id"])),
        }

    def _load_operations(self, connection: sqlite3.Connection, proposal_id: str) -> list[Operation]:
        rows = connection.execute(
            "SELECT * FROM op WHERE proposal = ? ORDER BY seq", (proposal_id,)
        ).fetchall()
        operations: list[Operation] = []
        for row in rows:
            data: dict[str, Any] = {
                "verb": row["verb"],
                "target": row["target"],
                "field": row["field"],
                "basis_rev": row["basis_rev"],
                "idem_key": row["idem_key"],
            }
            if row["from_val"] is not None:
                data["from"] = json.loads(row["from_val"])
            if row["to_val"] is not None:
                data["to"] = json.loads(row["to_val"])
            operations.append(Operation.model_validate(data))
        return operations

    def _normalize_read_set(self, reads: list[ReadSetEntry]) -> list[ReadSetEntry]:
        result: list[ReadSetEntry] = []
        seen: set[str] = set()
        for entry in reads:
            raw = entry.node.strip()
            node = (
                "book" if raw in {"book", "story://book"} else normalize_node_id(raw, self.ontology)
            )
            if node in seen:
                raise ProposalError(f"정규화 후 중복된 read_set 노드입니다: {node}")
            seen.add(node)
            result.append(ReadSetEntry(node=node, rev=entry.rev))
        return result

    @staticmethod
    def _revision_content(
        connection: sqlite3.Connection, node_id: str, rev: int
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT snapshot FROM node_revision WHERE node = ? AND rev = ?", (node_id, rev)
        ).fetchone()
        if row is None:
            return None
        snapshot = json.loads(row["snapshot"])
        return snapshot.get("content") if isinstance(snapshot, dict) else None

    @staticmethod
    def _on_behalf_of(connection: sqlite3.Connection, proposal_id: str) -> str | None:
        row = connection.execute(
            "SELECT on_behalf_of FROM proposal_actor WHERE proposal = ?", (proposal_id,)
        ).fetchone()
        return row["on_behalf_of"] if row is not None else None

    @staticmethod
    def _wire_value(operation: Operation, field: str) -> str | None:
        if field not in operation.model_fields_set:
            return None
        return canonical_json(getattr(operation, field))

    @staticmethod
    def _proposal_id() -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return f"proposal/{stamp}-{uuid.uuid4().hex[:10]}"

    @staticmethod
    def _dedupe_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for item in items:
            key = canonical_json(item)
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result
