"""The Juniper consulting her own documentation (ADR-025): local BM25, no LLM, no network."""

from brain.metadocs.context import docs_context, render, search
from brain.metadocs.detect import about_self
from brain.metadocs.index import available

__all__ = ["about_self", "available", "docs_context", "render", "search"]
