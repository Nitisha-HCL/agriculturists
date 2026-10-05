"""
Semantic/hybrid retrieval tool for the agriculture document knowledge base.

This tool accepts a plain-English question and retrieves the most relevant
document chunks from PostgreSQL using PGVectorStore.

The PostgreSQL database contains the RAG documents separately from the
structured agriculture tables used by the NL2SQL tool.
"""

import asyncio
import os
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
        "Pass a natural-language question, never SQL."
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

    def _run(self, question: str) -> str:

        try:

            # Sync API on purpose: it runs on PGEngine's own background loop,
            # so it also works when the caller already has a running event loop
            # (e.g. Jupyter), where asyncio.run() raises RuntimeError.
            results = self._get_vector_store().similarity_search(
                query=question,
                k=DEFAULT_K,
            )

        except Exception as exc:

            return (
                f"RAG retrieval failed: {type(exc).__name__}: {exc}"
            )

        if not results:

            return (
                "No relevant information was found in the "
                "agricultural document knowledge base."
            )

        output = []

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