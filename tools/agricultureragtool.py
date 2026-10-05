"""
Semantic/hybrid retrieval tool for the agriculture document knowledge base.

This tool accepts a plain-English question and retrieves the most relevant
document chunks from PostgreSQL using PGVectorStore.

The PostgreSQL database contains the RAG documents separately from the
structured agriculture tables used by the NL2SQL tool.
"""

import asyncio
import os
import re
import sys
from typing import Any

# ------------------------------------------------------------
# Windows event-loop fix
# Must happen before PGVectorStore/async PostgreSQL initialization.
# ------------------------------------------------------------

if sys.platform == "win32":
    asyncio.set_event_loop_policy(
        asyncio.WindowsSelectorEventLoopPolicy()
    )


from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr

from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGEngine, PGVectorStore
from langchain_postgres.v2.hybrid_search_config import (
    HybridSearchConfig,
    reciprocal_rank_fusion,
)


# ============================================================
# CONFIG
# ============================================================

# The RAG documents live in their own database (agriculture_rag), not in the
# agriculture database the NL2SQL tool uses, so this needs its own variable.
DB_URI = os.environ.get(
    "RAG_DB_URI",
    "postgresql://nitisha:nitisha@localhost:5432/agriculture_rag",
)

TABLE_NAME = os.environ.get(
    "RAG_TABLE_NAME",
    "rag_documents",
)

EMBEDDING_MODEL = os.environ.get(
    "RAG_EMBEDDING_MODEL",
    "text-embedding-3-small",
)

DEFAULT_K = 3
# Candidates fetched before crop filtering; chickpea.pdf alone holds ~75% of the
# chunks, so a plain top-3 search is easily swamped by off-crop passages.
FETCH_K = 15

# Crop names recognised in questions, mapped to the spelling used for matching.
CROP_SYNONYMS = {
    "cotton": "cotton",
    "chickpea": "chickpea",
    "gram": "chickpea",
    "chana": "chickpea",
    "rice": "rice",
    "paddy": "rice",
    "maize": "maize",
    "corn": "maize",
    "wheat": "wheat",
}

# Sources whose file name does not reveal their crop.
SOURCE_CROPS = {
    "iepf101.pdf": "rice",
}


def detect_crop(text: str) -> str | None:
    """The single crop a question is about, or None if it names zero or several."""
    found = {
        crop
        for word, crop in CROP_SYNONYMS.items()
        if re.search(rf"\b{word}s?\b", text or "", re.IGNORECASE)
    }
    return found.pop() if len(found) == 1 else None


def source_crop(source: str) -> str | None:
    """The crop a document is about, or None for crop-neutral documents."""
    if source in SOURCE_CROPS:
        return SOURCE_CROPS[source]
    return detect_crop(re.sub(r"[_\-.]+", " ", source))


def filter_by_crop(docs: list, crop: str | None, k: int = DEFAULT_K) -> tuple[list, bool]:
    """Keep documents about `crop` (or crop-neutral ones), de-duplicated, top k.

    Returns (docs, filtered). When nothing matches, returns the unfiltered top k
    and filtered=False so the caller can say so.
    """
    unique, seen = [], set()
    for doc in docs:
        key = (doc.metadata.get("source"), doc.metadata.get("page"), doc.page_content[:200])
        if key not in seen:
            seen.add(key)
            unique.append(doc)
    if not crop:
        return unique[:k], False
    matching = [d for d in unique if source_crop(d.metadata.get("source", "")) in (crop, None)]
    if matching:
        return matching[:k], True
    return unique[:k], False


# ============================================================
# INPUT SCHEMA
# ============================================================

class AgricultureRAGInput(BaseModel):
    question: str = Field(
        ...,
        description=(
            "A natural-language question describing the information you "
            "want to find in the agricultural document knowledge base. "
            "Do not write SQL."
        ),
    )
    crop: str | None = Field(
        default=None,
        description=(
            "The crop the question is about (e.g. 'cotton'), so only documents "
            "about that crop are searched. Optional: detected from the question "
            "when omitted."
        ),
    )


# ============================================================
# TOOL
# ============================================================

class AgricultureRAGTool(BaseTool):

    name: str = "agriculture_rag_search"

    description: str = (
        "Searches the agricultural document knowledge base using semantic "
        "and hybrid retrieval. Use this tool when information may be found "
        "in agricultural reports, crop-management documents, irrigation "
        "guidelines, farming practices, or other unstructured documents. "
        "Pass a natural-language question, never SQL, and the crop it is about; "
        "only documents about that crop are returned."
    )

    args_schema: type[BaseModel] = AgricultureRAGInput

    _engine: Any = PrivateAttr(default=None)
    _vector_store: Any = PrivateAttr(default=None)
    _embeddings: Any = PrivateAttr(default=None)

    # --------------------------------------------------------
    # Vector store
    # --------------------------------------------------------

    def _get_vector_store(self):

        if self._vector_store is not None:
            return self._vector_store

        self._engine = PGEngine.from_connection_string(
            url=DB_URI
        )

        self._embeddings = OpenAIEmbeddings(
            model=EMBEDDING_MODEL
        )

        self._vector_store = PGVectorStore.create_sync(
            engine=self._engine,
            table_name=TABLE_NAME,
            embedding_service=self._embeddings,
            hybrid_search_config=HybridSearchConfig(
                fusion_function=reciprocal_rank_fusion
            ),
        )

        return self._vector_store

    # --------------------------------------------------------
    # CrewAI tool entry point
    # --------------------------------------------------------

    def _run(self, question: str, crop: str | None = None) -> str:

        crop = CROP_SYNONYMS.get((crop or "").strip().lower()) or detect_crop(question)

        try:

            # Sync API on purpose: it runs on PGEngine's own background loop,
            # so it also works when the caller already has a running event loop
            # (e.g. Jupyter), where asyncio.run() raises RuntimeError.
            candidates = self._get_vector_store().similarity_search(
                query=question,
                k=FETCH_K,
            )

        except Exception as exc:

            return (
                f"RAG retrieval failed: {type(exc).__name__}: {exc}"
            )

        results, filtered = filter_by_crop(candidates, crop)

        if not results:

            return (
                "No relevant information was found in the "
                "agricultural document knowledge base."
            )

        output = []

        if crop and not filtered:
            output.append(
                f"NOTE: No documents about {crop} matched this question; the "
                "results below are about other crops and may not apply."
            )

        for i, doc in enumerate(results, start=1):

            source = doc.metadata.get(
                "source",
                "unknown",
            )

            page = doc.metadata.get(
                "page",
                None,
            )

            source_info = f"Source: {source}"

            if page is not None:
                source_info += f", Page: {page}"

            output.append(
                f"RESULT {i}\n"
                f"{source_info}\n"
                f"Content:\n{doc.page_content}"
            )

        return "\n\n---\n\n".join(output)


# ============================================================
# LOCAL TEST
# ============================================================

if __name__ == "__main__":

    tool = AgricultureRAGTool()

    result = tool.run(
        question=(
            "What irrigation management practices are recommended "
            "for maize?"
        )
    )

    print(result)