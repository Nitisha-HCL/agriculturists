"""Unit tests for the RAG tool's crop filter (no database, no embeddings)."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location(
    "agricultureragtool", Path(__file__).resolve().parent.parent / "tools" / "agricultureragtool.py"
)
rag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rag)


def doc(source, page=0, content="text"):
    return SimpleNamespace(metadata={"source": source, "page": page}, page_content=content)


@pytest.mark.parametrize("text, crop", [
    ("What pest management practices are recommended for cotton?", "cotton"),
    ("How is chickpea wilt managed?", "chickpea"),
    ("How should paddy be irrigated?", "rice"),
    ("Irrigation schedule for kharif maize", "maize"),
    ("Compare cotton and rice water needs", None),
    ("What is a good laptop for programming?", None),
    ("", None),
])
def test_detect_crop(text, crop):
    assert rag.detect_crop(text) == crop


@pytest.mark.parametrize("source, crop", [
    ("BN_Cotton.pdf", "cotton"),
    ("chickpea.pdf", "chickpea"),
    ("chickpea1.pdf", "chickpea"),  # "chickpea1" is not the word "chickpea": treated as neutral
    ("Rice-based-cropping-systems.pdf", "rice"),
    ("iepf101.pdf", "rice"),
    ("Irrigation Management __ Maize.pdf", "maize"),
    ("general-soil-health.pdf", None),
])
def test_source_crop(source, crop):
    assert rag.source_crop(source) == crop


def test_filter_keeps_matching_and_neutral_sources():
    docs = [doc("chickpea.pdf", 107), doc("BN_Cotton.pdf", 6), doc("general.pdf", 1), doc("BN_Cotton.pdf", 7)]
    kept, filtered = rag.filter_by_crop(docs, "cotton")
    assert filtered
    assert [d.metadata["source"] for d in kept] == ["BN_Cotton.pdf", "general.pdf", "BN_Cotton.pdf"]


def test_filter_removes_duplicates():
    docs = [doc("BN_Cotton.pdf", 6, "same"), doc("BN_Cotton.pdf", 6, "same"), doc("BN_Cotton.pdf", 7)]
    kept, _ = rag.filter_by_crop(docs, "cotton")
    assert len(kept) == 2


def test_filter_falls_back_when_nothing_matches():
    docs = [doc("chickpea.pdf", 1), doc("chickpea.pdf", 2)]
    kept, filtered = rag.filter_by_crop(docs, "cotton")
    assert not filtered and len(kept) == 2


def test_filter_without_crop_returns_top_k():
    docs = [doc("chickpea.pdf", i) for i in range(5)]
    kept, filtered = rag.filter_by_crop(docs, None)
    assert not filtered and len(kept) == rag.DEFAULT_K
