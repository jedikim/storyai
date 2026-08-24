"""Human-readable story address parsing and deterministic resolution."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from .ontology import Ontology


class AddressError(ValueError):
    """Base error for invalid or unresolved story references."""


class AddressNotFoundError(AddressError):
    pass


class AmbiguousAddressError(AddressError):
    def __init__(self, ref: str, candidates: Iterable[str]) -> None:
        self.ref = ref
        self.candidates = tuple(sorted(set(candidates)))
        joined = ", ".join(self.candidates)
        super().__init__(f"주소 {ref!r}가 모호합니다. 절대 주소를 사용하세요: {joined}")


@dataclass(frozen=True, slots=True)
class ParsedAddress:
    original: str
    value: str
    kind: str | None
    absolute: bool


@dataclass(frozen=True, slots=True)
class AddressCandidate:
    id: str
    kind: str
    title: str
    aliases: tuple[str, ...]


class CandidateSource(Protocol):
    def address_candidates(self) -> list[AddressCandidate]: ...


_INDEXED_DUPLICATE = re.compile(r"\[\d+\]$")


def _norm(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip().casefold()


def parse_address(ref: str, ontology: Ontology) -> ParsedAddress:
    if not isinstance(ref, str) or not ref.strip():
        raise AddressError("ref는 비어 있지 않은 문자열이어야 합니다")
    original = ref
    value = unicodedata.normalize("NFC", ref.strip())
    absolute = value.startswith("story://")
    if absolute:
        value = value[len("story://") :]
    value = value.strip("/")
    if not value or "?" in value or "#" in value or "\\" in value:
        raise AddressError(f"잘못된 story 주소입니다: {original!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise AddressError(f"잘못된 story 주소입니다: {original!r}")
    kind: str | None = None
    if len(parts) > 1:
        try:
            kind = ontology.canonical_kind(parts[0])
        except ValueError as exc:
            raise AddressError(str(exc)) from exc
        value = f"{kind.casefold()}/{'/'.join(parts[1:])}"
        absolute = True
    return ParsedAddress(original=original, value=value, kind=kind, absolute=absolute)


class AddressResolver:
    def __init__(self, ontology: Ontology, source: CandidateSource) -> None:
        self.ontology = ontology
        self.source = source

    def resolve(self, ref: str) -> str:
        parsed = parse_address(ref, self.ontology)
        query = _norm(parsed.value)
        scored: list[tuple[int, str]] = []
        for candidate in self.source.address_candidates():
            candidate_id = _norm(candidate.id)
            title = _norm(candidate.title)
            aliases = tuple(_norm(alias) for alias in candidate.aliases)
            if parsed.kind and _norm(candidate.kind) != _norm(parsed.kind):
                continue
            score = self._score(query, candidate_id, title, aliases, parsed.absolute)
            if score is not None:
                scored.append((score, candidate.id))
        if not scored:
            raise AddressNotFoundError(f"주소를 찾을 수 없습니다: {ref!r}")
        best = min(score for score, _ in scored)
        matches = [candidate_id for score, candidate_id in scored if score == best]
        if len(matches) > 1:
            raise AmbiguousAddressError(ref, matches)
        return matches[0]

    @staticmethod
    def _score(
        query: str,
        candidate_id: str,
        title: str,
        aliases: tuple[str, ...],
        absolute: bool,
    ) -> int | None:
        if query == candidate_id:
            return 0
        if absolute:
            return None
        suffix = candidate_id.rsplit("/", 1)[-1]
        if query == suffix:
            return 1
        if query == title:
            return 2
        if query in aliases:
            return 3
        if suffix.endswith(query) or title.endswith(query):
            return 4
        if any(alias.endswith(query) or query in alias for alias in aliases):
            return 5
        if not _INDEXED_DUPLICATE.search(suffix) and query in title:
            return 6
        return None
