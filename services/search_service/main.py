from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query

from services.search_service.config import get_search_settings
from services.search_service.es_client import ElasticsearchIndexer
from shared.errors import register_error_handlers
from shared.events import ALL_TOPICS, TOPIC_RAG_DOCUMENT_INGESTED, KafkaEventBus
from shared.schemas import DomainEvent, ElasticsearchResponse, RAGIngestRequest

settings = get_search_settings()
es_indexer = ElasticsearchIndexer(
    base_url=settings.elasticsearch_url,
    enabled=settings.elasticsearch_enabled,
)
event_bus = KafkaEventBus(
    service_name=settings.service_name,
    bootstrap_servers=settings.kafka_bootstrap_servers,
    enabled=settings.kafka_enabled,
)


async def handle_kafka_event(topic: str, event: DomainEvent) -> None:
    """Real-time Kafka -> Elasticsearch indexer for SPEC docs and operational audit events."""
    await es_indexer.index_document(
        index=settings.events_index,
        doc_id=event.event_id,
        document={
            "event_id": event.event_id,
            "topic": topic,
            "event_type": event.event_type,
            "source_service": event.source_service,
            "timestamp": event.timestamp.isoformat(),
            "summary": str(event.payload.get("summary") or event.payload.get("title") or event.event_type),
            "payload": event.payload,
        },
    )
    if topic == TOPIC_RAG_DOCUMENT_INGESTED and event.payload.get("doc_id"):
        await es_indexer.index_document(
            index=settings.specs_index,
            doc_id=str(event.payload["doc_id"]),
            document={
                "doc_id": event.payload["doc_id"],
                "title": event.payload.get("title", ""),
                "project": event.payload.get("project", "general"),
                "content": event.payload.get("content", ""),
                "source_url": event.payload.get("source_url") or "",
            },
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    await es_indexer.start()
    await event_bus.start_consumer(
        topics=ALL_TOPICS,
        group_id=settings.kafka_group_id,
        handler=handle_kafka_event,
    )
    try:
        yield
    finally:
        await event_bus.stop()
        await es_indexer.close()


app = FastAPI(
    title="DevWorkspace Search & Reporting Service",
    version="2.0.0",
    description="Elasticsearch full-text search & real-time Kafka event indexer",
    lifespan=lifespan,
)
register_error_handlers(app, settings.service_name)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "service": settings.service_name,
        "status": "ok",
        "elasticsearch_backend": es_indexer.backend_name,
        "kafka_enabled": settings.kafka_enabled,
    }


@app.post("/api/v1/search/specs/index")
async def index_spec_direct(req: RAGIngestRequest) -> dict[str, Any]:
    """Direct indexing endpoint for immediate sync alongside Kafka streaming."""
    await es_indexer.index_document(
        index=settings.specs_index,
        doc_id=req.doc_id,
        document=req.model_dump(),
    )
    return {"indexed": True, "doc_id": req.doc_id, "backend": es_indexer.backend_name}


@app.get("/api/v1/search/specs", response_model=ElasticsearchResponse)
async def search_specs(
    q: str = Query(..., min_length=1),
    project: str | None = None,
    size: int = Query(default=10, ge=1, le=50),
) -> ElasticsearchResponse:
    filters = {"project": project} if project else None
    hits = await es_indexer.search(
        index=settings.specs_index,
        query=q,
        fields=["title", "content", "project"],
        filters=filters,
        size=size,
    )
    return ElasticsearchResponse(
        query=q,
        total=len(hits),
        backend=es_indexer.backend_name,
        hits=hits,
    )


@app.get("/api/v1/search/events", response_model=ElasticsearchResponse)
async def search_events(
    q: str = Query(default="*", min_length=1),
    event_type: str | None = None,
    size: int = Query(default=20, ge=1, le=100),
) -> ElasticsearchResponse:
    filters = {"event_type": event_type} if event_type else None
    hits = await es_indexer.search(
        index=settings.events_index,
        query=q,
        fields=["event_type", "source_service", "topic", "summary"],
        filters=filters,
        size=size,
    )
    return ElasticsearchResponse(
        query=q,
        total=len(hits),
        backend=es_indexer.backend_name,
        hits=hits,
    )


@app.get("/api/v1/search/reports/summary")
async def reports_summary() -> dict[str, Any]:
    return await es_indexer.summary_counts(settings.specs_index, settings.events_index)
