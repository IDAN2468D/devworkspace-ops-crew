import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from shared.schemas import DomainEvent

logger = logging.getLogger(__name__)

# Canonical Kafka topics across DevWorkspace Ops Crew microservices
TOPIC_OPS_RUN_COMPLETED = "ops.run.completed"
TOPIC_HITL_ACTION_REQUESTED = "hitl.action.requested"
TOPIC_HITL_ACTION_DECIDED = "hitl.action.decided"
TOPIC_RAG_DOCUMENT_INGESTED = "rag.document.ingested"
TOPIC_AUTH_USER_AUTHENTICATED = "auth.user.authenticated"

ALL_TOPICS = [
    TOPIC_OPS_RUN_COMPLETED,
    TOPIC_HITL_ACTION_REQUESTED,
    TOPIC_HITL_ACTION_DECIDED,
    TOPIC_RAG_DOCUMENT_INGESTED,
    TOPIC_AUTH_USER_AUTHENTICATED,
]

EventHandler = Callable[[str, DomainEvent], Awaitable[None]]


class KafkaEventBus:
    """Async Kafka event bus backed by aiokafka with graceful in-memory fallback for local/tests."""

    def __init__(
        self,
        service_name: str,
        bootstrap_servers: str = "kafka:9092",
        enabled: bool = False,
    ) -> None:
        self.service_name = service_name
        self.bootstrap_servers = bootstrap_servers
        self.enabled = enabled
        self._producer: Any = None
        self._consumer: Any = None
        self._consumer_task: asyncio.Task[None] | None = None
        self.published_events: list[tuple[str, DomainEvent]] = []
        self._local_subscribers: dict[str, list[EventHandler]] = {}

    def subscribe_local(self, topics: list[str], handler: EventHandler) -> None:
        """Register an in-process subscriber (used in tests or when KAFKA_ENABLED=false)."""
        for topic in topics:
            self._local_subscribers.setdefault(topic, []).append(handler)

    async def start_producer(self) -> None:
        if not self.enabled:
            return
        try:
            from aiokafka import AIOKafkaProducer

            self._producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
            )
            await self._producer.start()
            logger.info("Kafka producer connected (%s) for %s", self.bootstrap_servers, self.service_name)
        except Exception as exc:
            logger.warning("Kafka producer unavailable (%s); falling back to local mode", exc)
            self._producer = None

    async def publish(
        self,
        topic: str,
        event_type: str,
        payload: dict[str, Any],
        key: str | None = None,
    ) -> DomainEvent:
        event = DomainEvent(
            event_type=event_type,
            source_service=self.service_name,
            payload=payload,
        )
        self.published_events.append((topic, event))

        if self._producer is not None:
            try:
                await self._producer.send_and_wait(
                    topic,
                    value=event.model_dump(mode="json"),
                    key=key or event.event_id,
                )
            except Exception as exc:
                logger.warning("Failed publishing Kafka event to %s: %s", topic, exc)

        for handler in self._local_subscribers.get(topic, []):
            await handler(topic, event)

        return event

    async def start_consumer(
        self,
        topics: list[str],
        group_id: str,
        handler: EventHandler,
    ) -> None:
        self.subscribe_local(topics, handler)
        if not self.enabled:
            return
        try:
            from aiokafka import AIOKafkaConsumer

            self._consumer = AIOKafkaConsumer(
                *topics,
                bootstrap_servers=self.bootstrap_servers,
                group_id=group_id,
                auto_offset_reset="earliest",
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            )
            await self._consumer.start()
            self._consumer_task = asyncio.create_task(self._consume_loop(handler))
            logger.info(
                "Kafka consumer started for %s on topics=%s group=%s",
                self.service_name,
                topics,
                group_id,
            )
        except Exception as exc:
            logger.warning("Kafka consumer unavailable (%s); running without broker", exc)
            self._consumer = None

    async def _consume_loop(self, handler: EventHandler) -> None:
        assert self._consumer is not None
        try:
            async for msg in self._consumer:
                try:
                    event = DomainEvent.model_validate(msg.value)
                    await handler(msg.topic, event)
                except Exception as exc:
                    logger.exception("Error processing Kafka message on %s: %s", msg.topic, exc)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
