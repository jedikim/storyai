from __future__ import annotations

from pathlib import Path

import pytest

from server.core.address import (
    AddressCandidate,
    AddressResolver,
    AmbiguousAddressError,
    parse_address,
)
from server.core.ontology import Ontology


class Source:
    def address_candidates(self) -> list[AddressCandidate]:
        return [
            AddressCandidate("character/한도영", "Character", "한도영", ("도영",)),
            AddressCandidate("object/젖은장갑", "Object", "젖은 장갑", ("장갑",)),
            AddressCandidate("object/검은장갑", "Object", "검은 장갑", ("장갑",)),
        ]


@pytest.fixture()
def resolver() -> AddressResolver:
    ontology = Ontology.load(Path(__file__).resolve().parents[1] / "spec" / "ontology.json")
    return AddressResolver(ontology, Source())


def test_absolute_and_story_uri_addresses_resolve(resolver: AddressResolver) -> None:
    assert resolver.resolve("character/한도영") == "character/한도영"
    assert resolver.resolve("story://character/한도영") == "character/한도영"


def test_alias_and_suffix_resolution(resolver: AddressResolver) -> None:
    assert resolver.resolve("도영") == "character/한도영"
    assert resolver.resolve("젖은장갑") == "object/젖은장갑"


def test_ambiguous_alias_requires_absolute_address(resolver: AddressResolver) -> None:
    with pytest.raises(AmbiguousAddressError, match="모호"):
        resolver.resolve("장갑")


def test_path_traversal_is_rejected(resolver: AddressResolver) -> None:
    with pytest.raises(ValueError, match="잘못된"):
        parse_address("story://character/../비밀", resolver.ontology)
