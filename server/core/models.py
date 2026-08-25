"""Validated P1 mutation contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Verb = Literal["ADD", "UPDATE", "INVALIDATE", "LINK", "UNLINK"]


class ReadSetEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str
    rev: int = Field(ge=0)

    @field_validator("node")
    @classmethod
    def node_is_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("read_set.node는 비어 있을 수 없습니다")
        return value.strip()


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    verb: Verb
    target: str
    field: str | None = None
    from_value: Any = Field(default=None, alias="from")
    to_value: Any = Field(default=None, alias="to")
    basis_rev: int | None = Field(default=None, ge=0)
    idem_key: str = Field(min_length=8, max_length=200)

    @field_validator("target", "idem_key")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("문자열 값은 비어 있을 수 없습니다")
        return value.strip()

    @model_validator(mode="after")
    def verb_shape(self) -> Operation:
        fields = self.model_fields_set
        if self.verb == "ADD":
            if "to_value" not in fields or not isinstance(self.to_value, dict):
                raise ValueError("ADD는 to 객체가 필요합니다")
            if self.field is not None:
                raise ValueError("ADD에는 field를 지정하지 않습니다")
        elif self.verb == "UPDATE":
            if not self.field or "to_value" not in fields:
                raise ValueError("UPDATE는 field와 to가 필요합니다")
        elif self.verb == "INVALIDATE":
            if self.field is not None or "to_value" in fields:
                raise ValueError("INVALIDATE에는 field/to를 지정하지 않습니다")
        elif self.verb in {"LINK", "UNLINK"}:
            if not self.field or not isinstance(self.to_value, str) or not self.to_value.strip():
                raise ValueError(f"{self.verb}는 field=관계와 to=대상 주소가 필요합니다")
        return self

    def wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=False)


class ProposalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ops: list[Operation] = Field(min_length=1, max_length=100)
    read_set: list[ReadSetEntry] = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(min_length=1, max_length=300)
    actor_kind: Literal["human", "agent", "cascade"] = "agent"
    model_id: str | None = Field(default=None, max_length=200)
    host: Literal["claude-code", "codex", "ui", "test"] = "codex"
    on_behalf_of: str | None = Field(default=None, max_length=300)
    parent_session_id: str | None = Field(default=None, max_length=300)

    @field_validator("rationale", "session_id")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("필수 문자열은 비어 있을 수 없습니다")
        return value

    @field_validator("parent_session_id")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("parent_session_id는 비어 있는 문자열일 수 없습니다")
        return value

    @model_validator(mode="after")
    def unique_entries(self) -> ProposalInput:
        nodes = [entry.node for entry in self.read_set]
        if len(nodes) != len(set(nodes)):
            raise ValueError("read_set에 같은 node를 두 번 넣을 수 없습니다")
        keys = [op.idem_key for op in self.ops]
        if len(keys) != len(set(keys)):
            raise ValueError("한 제안 안에서 idem_key는 중복될 수 없습니다")
        return self
