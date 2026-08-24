"""P2 visibility, scene-contract, and Promise projections."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from .database import connect_read_only

_MISSING = object()


class VisibilityService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).resolve()

    def classify(
        self,
        fact: str,
        *,
        character: str | None = None,
        as_of: int = 0,
        spoken: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(as_of, int) or isinstance(as_of, bool) or as_of < 0:
            raise ValueError("as_of는 0 이상의 정수여야 합니다")
        with connect_read_only(self.db_path) as connection:
            row = connection.execute(
                "SELECT 1 FROM live_node WHERE id = ? AND kind = 'Fact'", (fact,)
            ).fetchone()
            if row is None:
                raise ValueError(f"현재 유효한 Fact가 없습니다: {fact}")
            rows = connection.execute(
                """
                SELECT viewer, learned_at, pathway
                FROM visibility WHERE fact = ? ORDER BY viewer
                """,
                (fact,),
            ).fetchall()
        visible = {
            row["viewer"]
            for row in rows
            if row["learned_at"] is None or int(row["learned_at"]) <= as_of
        }
        reader_visible = "reader" in visible
        character_visible = character in visible if character is not None else None
        future_reader_reveal = any(
            row["viewer"] == "reader"
            and row["learned_at"] is not None
            and int(row["learned_at"]) > as_of
            for row in rows
        )
        return {
            "fact": fact,
            "as_of": as_of,
            "visible_to": sorted(visible),
            "dramatic_irony": bool(
                character is not None and reader_visible and not character_visible
            ),
            "mystery": not reader_visible and any(viewer != "reader" for viewer in visible),
            "twist": not reader_visible and future_reader_reveal,
            "continuity_bug": bool(spoken and character is not None and not character_visible),
        }


class ContractService:
    ALLOWED_OPS = {"eq", "ne", "in", "not_in", "exists", "not_exists"}

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).resolve()

    def feasible(self, scene: str, *, as_of: int | None = None) -> dict[str, Any]:
        if as_of is not None and (
            not isinstance(as_of, int) or isinstance(as_of, bool) or as_of < 0
        ):
            raise ValueError("as_of는 0 이상의 정수 또는 null이어야 합니다")
        with connect_read_only(self.db_path) as connection:
            row = connection.execute(
                "SELECT props, story_from FROM live_node WHERE id = ? AND kind = 'Scene'",
                (scene,),
            ).fetchone()
            if row is None:
                raise ValueError(f"현재 유효한 Scene이 없습니다: {scene}")
            props = self._json(row["props"], {})
            cutoff = as_of if as_of is not None else row["story_from"]
            failed_pre = self._failed(connection, props.get("pre", []), cutoff)
            active_forbid = self._matched(connection, props.get("forbid", []), cutoff)
        return {
            "scene": scene,
            "feasible": not failed_pre and not active_forbid,
            "failed_pre": failed_pre,
            "active_forbid": active_forbid,
            "post": props.get("post", []),
        }

    def _failed(
        self,
        connection: sqlite3.Connection,
        conditions: list[dict[str, Any]],
        cutoff: int | None,
    ) -> list[dict[str, Any]]:
        return [
            condition
            for condition in conditions
            if not self._matches(connection, condition, cutoff)
        ]

    def _matched(
        self,
        connection: sqlite3.Connection,
        conditions: list[dict[str, Any]],
        cutoff: int | None,
    ) -> list[dict[str, Any]]:
        return [
            condition for condition in conditions if self._matches(connection, condition, cutoff)
        ]

    def _matches(
        self,
        connection: sqlite3.Connection,
        condition: dict[str, Any],
        cutoff: int | None,
    ) -> bool:
        subject = condition["subject"]
        params: list[Any] = [subject]
        story_filter = ""
        if cutoff is not None:
            story_filter = " AND (story_from IS NULL OR story_from <= ?)"
            params.append(cutoff)
        row = connection.execute(
            f"SELECT * FROM live_node WHERE id = ?{story_filter}", params
        ).fetchone()
        actual = self._value(row, condition["field"])
        operator = condition.get("op", "eq")
        expected = condition.get("value")
        if operator == "exists":
            return actual is not _MISSING and actual is not None
        if operator == "not_exists":
            return actual is _MISSING or actual is None
        if actual is _MISSING:
            return False
        if operator == "eq":
            return actual == expected
        if operator == "ne":
            return actual != expected
        if operator == "in":
            return actual in expected
        if operator == "not_in":
            return actual not in expected
        raise ValueError(f"지원하지 않는 조건 연산자입니다: {operator}")

    @classmethod
    def _value(cls, row: sqlite3.Row | None, field: str) -> Any:
        if row is None:
            return _MISSING
        if field.startswith("props."):
            value: Any = cls._json(row["props"], {})
            for part in field.split(".")[1:]:
                if not isinstance(value, dict) or part not in value:
                    return _MISSING
                value = value[part]
            return value
        try:
            return row[field]
        except IndexError:
            return _MISSING

    @staticmethod
    def _json(value: str | None, fallback: Any) -> Any:
        if value is None:
            return fallback
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return fallback


class PromiseService:
    STATUSES = {"hypothetical", "eligible", "actualized", "prevented"}

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).resolve()

    def list(
        self,
        *,
        statuses: list[str] | None = None,
        as_of: int | None = None,
        sort: Literal["debt", "age", "s_eff"] = "debt",
    ) -> list[dict[str, Any]]:
        selected = set(statuses or self.STATUSES)
        if statuses is not None and not statuses:
            raise ValueError("status는 비어 있지 않은 배열이어야 합니다")
        unknown = sorted(selected - self.STATUSES)
        if unknown:
            raise ValueError(f"알 수 없는 Promise 상태: {', '.join(unknown)}")
        if as_of is not None and (
            not isinstance(as_of, int) or isinstance(as_of, bool) or as_of < 0
        ):
            raise ValueError("as_of는 0 이상의 정수 또는 null이어야 합니다")
        if sort not in {"debt", "age", "s_eff"}:
            raise ValueError("sort는 debt, age, s_eff 중 하나여야 합니다")
        with connect_read_only(self.db_path) as connection:
            placeholders = ",".join("?" for _ in selected)
            reveal_filter = "" if as_of is None else "AND (reveal_at IS NULL OR reveal_at <= ?)"
            params: list[Any] = [*sorted(selected)]
            if as_of is not None:
                params.append(as_of)
            rows = connection.execute(
                f"""
                SELECT * FROM live_node
                WHERE kind = 'Promise'
                  AND COALESCE(json_extract(props, '$.status'), 'hypothetical')
                      IN ({placeholders})
                  {reveal_filter}
                ORDER BY id
                """,
                params,
            ).fetchall()
            horizon_row = connection.execute(
                "SELECT COALESCE(MAX(story_from), 0) AS value FROM live_node"
            ).fetchone()
            horizon = as_of if as_of is not None else int(horizon_row["value"] or 0)
            result = [self._project(connection, row, horizon, as_of) for row in rows]
        if sort in {"debt", "age"}:
            return sorted(result, key=lambda item: (-item[sort], item["id"]))
        return sorted(result, key=lambda item: (item[sort], item["id"]))

    def _project(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        horizon: int,
        as_of: int | None,
    ) -> dict[str, Any]:
        props = json.loads(row["props"] or "{}")
        planted = self._edge_scenes(connection, row["id"], "plants", incoming=True, as_of=as_of)
        triggers = self._edge_scenes(
            connection, row["id"], "requires_trigger", incoming=False, as_of=as_of
        )
        payoffs = self._edge_scenes(connection, row["id"], "pays_off", incoming=True, as_of=as_of)
        f_values = self._merge_refs(props.get("F"), planted)
        t_values = self._merge_refs(props.get("T"), triggers)
        p_values = self._merge_refs(props.get("P"), payoffs)
        planted_at = self._minimum_story(connection, f_values)
        trigger_at = self._minimum_story(connection, t_values)
        age = max(0, horizon - planted_at) if planted_at is not None else 0
        status = props.get("status", "hypothetical")
        explicit_debt = props.get("debt")
        debt = (
            float(explicit_debt)
            if isinstance(explicit_debt, (int, float)) and not isinstance(explicit_debt, bool)
            else round((age + 1) * (1.5 if status == "eligible" else 1.0), 3)
        )
        explicit_s_eff = props.get("s_eff")
        if isinstance(explicit_s_eff, (int, float)) and not isinstance(explicit_s_eff, bool):
            s_eff = float(explicit_s_eff)
        elif planted_at is not None and trigger_at is not None:
            s_eff = round(1 / (1 + max(0, trigger_at - planted_at)), 3)
        else:
            s_eff = 1.0
        explicit_delta = props.get("delta_coh")
        if isinstance(explicit_delta, (int, float)) and not isinstance(explicit_delta, bool):
            delta_coh = float(explicit_delta)
        else:
            delta_coh = round((bool(f_values) + bool(t_values)) / 2, 3)
        return {
            "id": row["id"],
            "title": row["title"],
            "F": f_values,
            "T": t_values,
            "P": p_values,
            "status": status,
            "debt": debt,
            "age": age,
            "s_eff": s_eff,
            "delta_coh": delta_coh,
        }

    @staticmethod
    def _merge_refs(explicit: Any, inferred: list[str]) -> list[str]:
        values = list(inferred)
        if isinstance(explicit, str):
            values.append(explicit)
        return sorted(set(values))

    @staticmethod
    def _edge_scenes(
        connection: sqlite3.Connection,
        promise: str,
        relation: str,
        *,
        incoming: bool,
        as_of: int | None,
    ) -> list[str]:
        endpoint = "e.src" if incoming else "e.dst"
        predicate = "e.dst = ?" if incoming else "e.src = ?"
        params: list[Any] = [promise, relation]
        cutoff = ""
        if as_of is not None:
            cutoff = (
                "AND (s.story_from IS NULL OR s.story_from <= ?) "
                "AND (e.story_from IS NULL OR e.story_from <= ?) "
                "AND (e.story_to IS NULL OR e.story_to >= ?)"
            )
            params.extend([as_of, as_of, as_of])
        rows = connection.execute(
            f"""
            SELECT {endpoint} AS scene
            FROM live_edge AS e JOIN live_node AS s ON s.id = {endpoint}
            WHERE {predicate} AND e.rel = ? {cutoff}
            ORDER BY scene
            """,
            params,
        ).fetchall()
        return [row["scene"] for row in rows]

    @staticmethod
    def _minimum_story(connection: sqlite3.Connection, nodes: list[str]) -> int | None:
        if not nodes:
            return None
        placeholders = ",".join("?" for _ in nodes)
        row = connection.execute(
            f"SELECT MIN(story_from) AS value FROM live_node WHERE id IN ({placeholders})",
            nodes,
        ).fetchone()
        return int(row["value"]) if row["value"] is not None else None
