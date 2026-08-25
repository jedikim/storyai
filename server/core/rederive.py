"""P6 queued Tier-2 rederive worker and provider contract."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from .database import connect_read_only, connect_write
from .merkle import canonical_json, node_content
from .ops import field_value
from .write_service import WriteService


class RederiveProvider(Protocol):
    def rederive(self, request: dict[str, Any]) -> dict[str, Any]: ...


class TerminalJobError(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class WebhookRederiveProvider:
    """Small vendor-neutral JSON webhook adapter for a configured LLM gateway."""

    def __init__(
        self,
        endpoint: str,
        *,
        api_key: str | None = None,
        timeout_sec: float = 30.0,
    ) -> None:
        parsed = urlparse(endpoint)
        loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
        ):
            raise ValueError("rederive endpoint는 HTTPS 또는 loopback HTTP여야 합니다")
        if not 1 <= timeout_sec <= 120:
            raise ValueError("rederive timeout은 1..120초 범위여야 합니다")
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout_sec = timeout_sec

    @classmethod
    def from_environment(cls) -> WebhookRederiveProvider:
        endpoint = os.environ.get("STORYAI_REDERIVE_ENDPOINT")
        if not endpoint:
            raise ValueError("STORYAI_REDERIVE_ENDPOINT가 설정되지 않았습니다")
        raw_timeout = os.environ.get("STORYAI_REDERIVE_TIMEOUT_SEC", "30")
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ValueError("STORYAI_REDERIVE_TIMEOUT_SEC가 숫자가 아닙니다") from exc
        return cls(
            endpoint,
            api_key=os.environ.get("STORYAI_REDERIVE_API_KEY"),
            timeout_sec=timeout,
        )

    def rederive(self, request: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if isinstance(request.get("job_id"), str):
            headers["Idempotency-Key"] = request["job_id"]
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        wire = urllib.request.Request(
            self.endpoint,
            data=canonical_json(request).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            opener = urllib.request.build_opener(_NoRedirect)
            with opener.open(wire, timeout=self.timeout_sec) as response:
                raw = response.read(1_000_001)
        except (OSError, urllib.error.URLError) as exc:
            raise RuntimeError(f"rederive provider 호출 실패: {exc}") from exc
        if len(raw) > 1_000_000:
            raise RuntimeError("rederive provider 응답이 1MB를 초과했습니다")
        try:
            result = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("rederive provider가 유효한 JSON을 반환하지 않았습니다") from exc
        if (
            not isinstance(result, dict)
            or "value" not in result
            or not isinstance(result.get("model_id"), str)
            or not result["model_id"].strip()
            or len(result["model_id"]) > 200
        ):
            raise RuntimeError(
                "rederive provider 응답에는 value와 비어 있지 않은 model_id가 필요합니다"
            )
        return result


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: str
    run: str
    node: str
    depth: int
    sources: list[str]
    target_field: str
    instruction: str
    original_rev: int
    target_rev: int
    source_revs: dict[str, int]
    max_tokens: int
    attempts: int
    claim_token: str


class CascadeWorker:
    def __init__(
        self,
        *,
        db_path: str | Path,
        writer: WriteService,
        provider: RederiveProvider,
        lease_sec: int = 120,
        max_attempts: int = 3,
    ) -> None:
        if not 10 <= lease_sec <= 3600:
            raise ValueError("worker lease_sec는 10..3600 범위여야 합니다")
        if not 1 <= max_attempts <= 10:
            raise ValueError("worker max_attempts는 1..10 범위여야 합니다")
        self.db_path = Path(db_path).resolve()
        self.writer = writer
        self.provider = provider
        self.lease_sec = lease_sec
        self.max_attempts = max_attempts

    def run_once(self) -> dict[str, Any]:
        job = self._claim()
        if job is None:
            return {"status": "idle"}
        try:
            context = self._context(job)
            existing = self._existing_proposal(job)
            if existing is not None:
                finished = self._finish(
                    job,
                    status="proposed",
                    proposal_id=existing["proposal_id"],
                    error=None,
                )
                if not finished:
                    return {"status": "lost_lease", "job_id": job.id}
                return {
                    "status": "proposed",
                    "job_id": job.id,
                    "proposal_id": existing["proposal_id"],
                    "proposal_status": existing["proposal_status"],
                    "recovered": True,
                }
            request = {
                "job_id": job.id,
                "instruction": job.instruction,
                "target_field": job.target_field,
                "original_human_node": context["original"],
                "changed_sources": context["sources"],
                "max_tokens": job.max_tokens,
            }
            estimated_input = self._estimate_tokens(request)
            if estimated_input + job.max_tokens > self.writer.cascade.max_tokens:
                return self._terminal(job, "token_budget_exceeded")
            response = self.provider.rederive(request)
            value = response["value"]
            model_id = response.get("model_id")
            if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 200:
                return self._terminal(job, "provider_model_id_invalid")
            if self._estimate_tokens(value) > job.max_tokens:
                return self._terminal(job, "provider_output_budget_exceeded")
            if value == context["current_value"]:
                if not self._finish(job, status="skipped", proposal_id=None, error=None):
                    return {"status": "lost_lease", "job_id": job.id}
                return {"status": "skipped", "job_id": job.id, "reason": "unchanged"}
            if not self._owns(job):
                return {"status": "lost_lease", "job_id": job.id}
            identity = canonical_json(
                {
                    "job": job.id,
                    "target_rev": context["target_rev"],
                    "sources": job.source_revs,
                    "value": value,
                }
            )
            try:
                proposal = self.writer.propose(
                    ops=[
                        {
                            "verb": "UPDATE",
                            "target": job.node,
                            "field": job.target_field,
                            "from": context["current_value"],
                            "to": value,
                            "basis_rev": context["target_rev"],
                            "idem_key": "tier2-" + hashlib.sha256(identity.encode()).hexdigest(),
                        }
                    ],
                    read_set=[
                        {"node": node, "rev": revision}
                        for node, revision in sorted(
                            {job.node: context["target_rev"], **job.source_revs}.items()
                        )
                    ],
                    rationale=f"Domino v2 Tier-2 rederive job {job.id}",
                    session_id=f"tier2:{job.id}",
                    actor_kind="cascade",
                    model_id=model_id.strip(),
                    host="codex",
                    parent_session_id=context["parent_session_id"],
                )
            except ValueError as exc:
                raise TerminalJobError(f"Tier-2 Proposal 생성 실패: {exc}") from exc
            finished = self._finish(
                job,
                status="proposed",
                proposal_id=str(proposal["proposal_id"]),
                error=None,
            )
            if not finished:
                return {
                    "status": "lost_lease",
                    "job_id": job.id,
                    "orphan_proposal_id": proposal["proposal_id"],
                }
            return {
                "status": "proposed",
                "job_id": job.id,
                "proposal_id": proposal["proposal_id"],
                "proposal_status": proposal["status"],
            }
        except TerminalJobError as exc:
            return self._terminal(job, str(exc))
        except (ValueError, RuntimeError, sqlite3.Error) as exc:
            return self._retry_or_fail(job, str(exc))

    def run(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("worker limit는 1..1000 범위여야 합니다")
        results: list[dict[str, Any]] = []
        for _ in range(limit):
            result = self.run_once()
            results.append(result)
            if result["status"] in {"idle", "queued"}:
                break
        return results

    def _claim(self) -> ClaimedJob | None:
        now_value = datetime.now(UTC)
        now = now_value.isoformat()
        lease_until = (now_value + timedelta(seconds=self.lease_sec)).isoformat()
        claim_token = uuid.uuid4().hex
        with connect_write(self.db_path) as connection:
            exhausted = connection.execute(
                """
                SELECT run, node FROM cascade_job
                WHERE status = 'running' AND lease_until <= ? AND attempts >= ?
                """,
                (now, self.max_attempts),
            ).fetchall()
            connection.execute(
                """
                UPDATE cascade_job
                SET status = 'queued', lease_until = NULL, claim_token = NULL, updated_at = ?
                WHERE status = 'running' AND lease_until <= ? AND attempts < ?
                """,
                (now, now, self.max_attempts),
            )
            connection.execute(
                """
                UPDATE cascade_job
                SET status = 'failed', lease_until = NULL, claim_token = NULL,
                    error = 'worker lease expired after max attempts', updated_at = ?
                WHERE status = 'running' AND lease_until <= ? AND attempts >= ?
                """,
                (now, now, self.max_attempts),
            )
            for item in exhausted:
                active = connection.execute(
                    """
                    SELECT 1 FROM cascade_job
                    WHERE run = ? AND node = ? AND status IN ('queued','running')
                    LIMIT 1
                    """,
                    (item["run"], item["node"]),
                ).fetchone()
                if active is None:
                    connection.execute(
                        """
                        UPDATE cascade_item
                        SET status = 'blocked', reason = 'tier2_worker_lease_exhausted'
                        WHERE run = ? AND node = ? AND status = 'queued'
                        """,
                        (item["run"], item["node"]),
                    )
            row = connection.execute(
                """
                SELECT * FROM cascade_job
                WHERE status = 'queued' AND attempts < ?
                ORDER BY ts, id LIMIT 1
                """,
                (self.max_attempts,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE cascade_job
                SET status = 'running', attempts = attempts + 1,
                    lease_until = ?, claim_token = ?, error = NULL, updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (lease_until, claim_token, now, row["id"]),
            )
            return ClaimedJob(
                id=str(row["id"]),
                run=str(row["run"]),
                node=str(row["node"]),
                depth=int(row["depth"]),
                sources=list(json.loads(row["sources"])),
                target_field=str(row["target_field"]),
                instruction=str(row["instruction"]),
                original_rev=int(row["original_rev"]),
                target_rev=int(row["target_rev"]),
                source_revs={
                    str(key): int(value) for key, value in json.loads(row["source_revs"]).items()
                },
                max_tokens=int(row["max_tokens"]),
                attempts=int(row["attempts"]) + 1,
                claim_token=claim_token,
            )

    def _context(self, job: ClaimedJob) -> dict[str, Any]:
        with connect_read_only(self.db_path) as connection:
            original_row = connection.execute(
                "SELECT snapshot FROM node_revision WHERE node = ? AND rev = ?",
                (job.node, job.original_rev),
            ).fetchone()
            if original_row is None:
                raise TerminalJobError("original human revision이 없습니다")
            original_snapshot = json.loads(original_row["snapshot"])
            if original_snapshot.get("origin") != "human":
                raise TerminalJobError("original revision의 origin이 human이 아닙니다")
            target = connection.execute(
                "SELECT rev FROM live_node WHERE id = ?", (job.node,)
            ).fetchone()
            if target is None:
                raise TerminalJobError("Tier-2 target이 live 상태가 아닙니다")
            if int(target["rev"]) != job.target_rev:
                raise TerminalJobError("Tier-2 target이 queue 이후 변경되었습니다")
            target_content = node_content(connection, job.node)
            source_values: list[dict[str, Any]] = []
            for source in job.sources:
                row = connection.execute(
                    "SELECT rev FROM live_node WHERE id = ?", (source,)
                ).fetchone()
                if row is None or int(row["rev"]) != job.source_revs[source]:
                    raise TerminalJobError(f"Tier-2 source가 queue 이후 변경되었습니다: {source}")
                content = node_content(connection, source)
                source_values.append(
                    {
                        "id": source,
                        "rev": int(row["rev"]),
                        "typed": {
                            key: content.get(key) for key in self.writer.cascade._PROJECTION_FIELDS
                        },
                    }
                )
            parent = connection.execute(
                """
                SELECT p.session_id
                FROM cascade_run AS run
                JOIN op AS o ON o.id = run.trigger_op
                JOIN proposal AS p ON p.id = o.proposal
                WHERE run.id = ?
                """,
                (job.run,),
            ).fetchone()
            if parent is None or not parent["session_id"]:
                raise TerminalJobError("Tier-2 parent session을 찾을 수 없습니다")
            return {
                "original": original_snapshot["content"],
                "sources": source_values,
                "current_value": field_value(target_content, job.target_field),
                "target_rev": int(target["rev"]),
                "parent_session_id": str(parent["session_id"]),
            }

    def _existing_proposal(self, job: ClaimedJob) -> dict[str, str] | None:
        with connect_read_only(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT p.id, p.status
                FROM proposal AS p
                JOIN op AS o ON o.proposal = p.id AND o.seq = 0
                WHERE p.session_id = ? AND p.actor_kind = 'cascade'
                  AND p.status IN ('open','accepted')
                  AND o.verb = 'UPDATE' AND o.target = ? AND o.field = ?
                  AND o.basis_rev = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM op AS extra
                    WHERE extra.proposal = p.id AND extra.seq <> 0
                  )
                ORDER BY p.ts, p.id LIMIT 1
                """,
                (f"tier2:{job.id}", job.node, job.target_field, job.target_rev),
            ).fetchone()
        if row is None:
            return None
        return {"proposal_id": str(row["id"]), "proposal_status": str(row["status"])}

    def _retry_or_fail(self, job: ClaimedJob, error: str) -> dict[str, Any]:
        status = "failed" if job.attempts >= self.max_attempts else "queued"
        if status == "failed":
            finished = self._finish(job, status="failed", proposal_id=None, error=error[:2000])
            return {
                "status": status if finished else "lost_lease",
                "job_id": job.id,
                "error": error,
            }
        with connect_write(self.db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE cascade_job
                SET status = ?, lease_until = NULL, claim_token = NULL,
                    error = ?, updated_at = ?
                WHERE id = ? AND status = 'running' AND claim_token = ?
                """,
                (
                    status,
                    error[:2000],
                    datetime.now(UTC).isoformat(),
                    job.id,
                    job.claim_token,
                ),
            )
        return {
            "status": status if cursor.rowcount == 1 else "lost_lease",
            "job_id": job.id,
            "error": error,
        }

    def _terminal(self, job: ClaimedJob, error: str) -> dict[str, Any]:
        finished = self._finish(job, status="failed", proposal_id=None, error=error)
        return {
            "status": "failed" if finished else "lost_lease",
            "job_id": job.id,
            "error": error,
        }

    def _finish(
        self,
        job: ClaimedJob,
        *,
        status: str,
        proposal_id: str | None,
        error: str | None,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        with connect_write(self.db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE cascade_job
                SET status = ?, lease_until = NULL, claim_token = NULL,
                    proposal = ?, error = ?, updated_at = ?
                WHERE id = ? AND status = 'running' AND claim_token = ?
                """,
                (status, proposal_id, error, now, job.id, job.claim_token),
            )
            if cursor.rowcount != 1:
                return False
            if proposal_id is not None:
                connection.execute(
                    """
                    UPDATE cascade_item
                    SET status = 'proposed', proposal = ?, reason = 'tier2_proposal_ready'
                    WHERE run = ? AND node = ? AND status = 'queued'
                    """,
                    (proposal_id, job.run, job.node),
                )
            elif status in {"failed", "skipped"}:
                active = connection.execute(
                    """
                    SELECT 1 FROM cascade_job
                    WHERE run = ? AND node = ? AND status IN ('queued','running')
                    LIMIT 1
                    """,
                    (job.run, job.node),
                ).fetchone()
                if active is None:
                    failed = connection.execute(
                        """
                        SELECT 1 FROM cascade_job
                        WHERE run = ? AND node = ? AND status = 'failed' LIMIT 1
                        """,
                        (job.run, job.node),
                    ).fetchone()
                    connection.execute(
                        """
                        UPDATE cascade_item
                        SET status = ?, reason = ?
                        WHERE run = ? AND node = ? AND status = 'queued'
                        """,
                        (
                            "blocked" if failed is not None else "cutoff",
                            "tier2_failed" if failed is not None else "tier2_unchanged",
                            job.run,
                            job.node,
                        ),
                    )
        return True

    def _owns(self, job: ClaimedJob) -> bool:
        with connect_read_only(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM cascade_job
                WHERE id = ? AND status = 'running' AND claim_token = ?
                  AND lease_until > ?
                """,
                (job.id, job.claim_token, datetime.now(UTC).isoformat()),
            ).fetchone()
        return row is not None

    @staticmethod
    def _estimate_tokens(value: Any) -> int:
        return max(1, (len(canonical_json(value).encode("utf-8")) + 3) // 4)
