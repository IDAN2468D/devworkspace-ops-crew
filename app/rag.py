from pathlib import Path
from typing import Any

import chromadb

from app.config import get_settings
from app.schemas import RAGChunk

settings = get_settings()


def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + chunk_size, len(cleaned))
        chunks.append(cleaned[start:end])
        if end == len(cleaned):
            break
        start = max(0, end - overlap)
    return chunks


class DriveRAGStore:
    """Persistent ChromaDB vector store for indexing Google Drive SPEC/PRD docs."""

    def __init__(
        self,
        persist_dir: str | None = None,
        collection_name: str | None = None,
    ) -> None:
        persist_path = Path(persist_dir or settings.chroma_persist_dir)
        persist_path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist_path))
        self._collection = self._client.get_or_create_collection(
            name=collection_name or settings.chroma_collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert_document(
        self,
        doc_id: str,
        title: str,
        content: str,
        project: str = "general",
        source_url: str | None = None,
    ) -> int:
        chunks = _chunk_text(content)
        if not chunks:
            return 0

        ids = [f"{doc_id}::chunk_{idx}" for idx in range(len(chunks))]
        metadatas: list[dict[str, Any]] = [
            {
                "doc_id": doc_id,
                "title": title,
                "project": project,
                "source_url": source_url or "",
                "chunk_index": idx,
            }
            for idx in range(len(chunks))
        ]
        self._collection.upsert(ids=ids, documents=chunks, metadatas=metadatas)
        return len(chunks)

    def search(self, query: str, top_k: int = 4, project: str | None = None) -> list[RAGChunk]:
        if self._collection.count() == 0:
            return []

        where_filter = {"project": project} if project else None
        raw = self._collection.query(
            query_texts=[query],
            n_results=min(top_k, self._collection.count()),
            where=where_filter,
        )
        ids = raw.get("ids", [[]])[0]
        docs = raw.get("documents", [[]])[0]
        metas = raw.get("metadatas", [[]])[0]
        distances = raw.get("distances", [[]])[0]

        results: list[RAGChunk] = []
        for chunk_id, text, meta, dist in zip(ids, docs, metas, distances):
            results.append(
                RAGChunk(
                    chunk_id=chunk_id,
                    title=str(meta.get("title", "Untitled")),
                    project=str(meta.get("project", "general")),
                    text=text,
                    distance=round(float(dist), 4) if dist is not None else None,
                )
            )
        return results

    def seed_sample_specs_if_empty(self) -> None:
        if self._collection.count() > 0:
            return
        self.upsert_document(
            doc_id="drv_spec_001",
            title="DevWorkspace_SPEC_v1.md",
            project="devworkspace-ops-crew",
            content=(
                "DevWorkspace Ops Crew Architecture SPEC: "
                "All write actions (sending emails, moving Drive files, creating GitHub issues) "
                "MUST pass through the SQLAlchemy-backed HITL Approval Gate. "
                "Read operations use read-only MCP connectors for Gmail, Google Drive, and GitHub."
            ),
        )
        self.upsert_document(
            doc_id="drv_spec_002",
            title="MCP_AI_IDE_Core_Architecture.md",
            project="mcp-ai-ide-core",
            content=(
                "mcp-ai-ide-core uses 4 MCP servers, ChromaDB RAG for codebase context, "
                "and strict HITL security checks in test_hitl_gate.py before shell or git writes."
            ),
        )


drive_rag_store = DriveRAGStore()
