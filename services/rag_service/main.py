from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query

from services.rag_service.config import get_rag_settings
from services.rag_service.store import DriveRAGStore
from shared.errors import register_error_handlers
from shared.events import TOPIC_RAG_DOCUMENT_INGESTED, KafkaEventBus
from shared.schemas import RAGIngestRequest, RAGSearchResponse

settings = get_rag_settings()
drive_rag_store = DriveRAGStore()
event_bus = KafkaEventBus(
    service_name=settings.service_name,
    bootstrap_servers=settings.kafka_bootstrap_servers,
    enabled=settings.kafka_enabled,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    seeded = drive_rag_store.seed_sample_specs_if_empty()
    await event_bus.start_producer()
    for doc in seeded:
        await event_bus.publish(
            topic=TOPIC_RAG_DOCUMENT_INGESTED,
            event_type="rag.document.ingested",
            key=doc["doc_id"],
            payload=doc,
        )
    try:
        yield
    finally:
        await event_bus.stop()


app = FastAPI(
    title="DevWorkspace RAG Service",
    version="2.0.0",
    description="Semantic Vector Search Microservice (ChromaDB) publishing Kafka ingestion events",
    lifespan=lifespan,
)
register_error_handlers(app, settings.service_name)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "service": settings.service_name,
        "status": "ok",
        "kafka_enabled": settings.kafka_enabled,
        "collection": settings.chroma_collection_name,
    }


@app.post("/api/v1/rag/ingest")
async def ingest_spec_doc(req: RAGIngestRequest) -> dict[str, Any]:
    chunks = drive_rag_store.upsert_document(
        doc_id=req.doc_id,
        title=req.title,
        content=req.content,
        project=req.project,
        source_url=req.source_url,
    )
    await event_bus.publish(
        topic=TOPIC_RAG_DOCUMENT_INGESTED,
        event_type="rag.document.ingested",
        key=req.doc_id,
        payload={
            "doc_id": req.doc_id,
            "title": req.title,
            "content": req.content,
            "project": req.project,
            "source_url": req.source_url,
            "chunks_indexed": chunks,
        },
    )
    return {"doc_id": req.doc_id, "chunks_indexed": chunks}


@app.get("/api/v1/rag/search", response_model=RAGSearchResponse)
def search_rag(
    q: str = Query(..., min_length=2),
    project: str | None = None,
    top_k: int = Query(default=4, ge=1, le=10),
) -> RAGSearchResponse:
    return RAGSearchResponse(
        query=q,
        results=drive_rag_store.search(query=q, top_k=top_k, project=project),
    )
