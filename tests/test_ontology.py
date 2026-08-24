from __future__ import annotations

from pathlib import Path

import pytest

from server.core.ontology import Ontology, OntologyError


def ontology() -> Ontology:
    return Ontology.load(Path(__file__).resolve().parents[1] / "spec" / "ontology.json")


def test_p0_exposes_exactly_six_public_kinds() -> None:
    assert ontology().p0_kinds == (
        "Character",
        "Location",
        "Object",
        "Rule",
        "Scene",
        "Promise",
    )


def test_kind_lookup_is_case_insensitive() -> None:
    assert ontology().canonical_kind("character", p0_only=True) == "Character"


def test_edge_endpoint_constraints_are_enforced() -> None:
    with pytest.raises(OntologyError, match="src는 Character"):
        ontology().validate_edge("performs", "Scene", "Scene")
