"""Reusable graph core shared by MCP and future HTTP adapters."""

from .ontology import Ontology, OntologyError
from .service import StoryService

__all__ = ["Ontology", "OntologyError", "StoryService"]
