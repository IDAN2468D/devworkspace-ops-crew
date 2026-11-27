import logging
from typing import Any

import httpx

from shared.schemas import SearchHit

logger = logging.getLogger(__name__)


class ElasticsearchIndexer:
    """Async Elasticsearch 8 REST indexer with an in-memory fallback for local/pytest runs."""

    def __init__(self, base_url: str, enabled: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.enabled = enabled
        self._client: httpx.AsyncClient | None = None
        self._memory_indices: dict[str, dict[str, dict[str, Any]]] = {}

    @property
    def backend_name(self) -> str:
        return "elasticsearch" if (self.enabled and self._client is not None) else "in-memory"

    async def start(self) -> None:
        if not self.enabled:
            return
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)
        try:
            resp = await self._client.get("/")
            resp.raise_for_status()
            logger.info("Connected to Elasticsearch at %s", self.base_url)
        except Exception as exc:
            logger.warning("Elasticsearch unavailable (%s); falling back to in-memory search", exc)
            await self._client.aclose()
            self._client = None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def index_document(self, index: str, doc_id: str, document: dict[str, Any]) -> None:
        self._memory_indices.setdefault(index, {})[doc_id] = document
        if self._client is not None:
            try:
                resp = await self._client.put(
                    f"/{index}/_doc/{doc_id}",
                    params={"refresh": "true"},
                    json=document,
                )
                resp.raise_for_status()
            except Exception as exc:
                logger.warning("Elasticsearch indexing failed for %s/%s: %s", index, doc_id, exc)

    async def search(
        self,
        index: str,
        query: str,
        fields: list[str],
        filters: dict[str, str] | None = None,
        size: int = 10,
    ) -> list[SearchHit]:
        if self._client is not None:
            try:
                must_clauses: list[dict[str, Any]] = [
                    {"multi_match": {"query": query, "fields": fields, "fuzziness": "AUTO"}}
                ]
                filter_clauses = [
                    {"term": {f"{k}.keyword": v}} for k, v in (filters or {}).items() if v
                ]
                body = {
                    "size": size,
                    "query": {"bool": {"must": must_clauses, "filter": filter_clauses}},
                    "highlight": {"fields": {f: {} for f in fields}},
                }
                resp = await self._client.post(f"/{index}/_search", json=body)
                if resp.status_code == 404:
                    return []
                resp.raise_for_status()
                raw_hits = resp.json().get("hits", {}).get("hits", [])
                results: list[SearchHit] = []
                for item in raw_hits:
                    hl_map = item.get("highlight", {})
                    highlights = [frag for frags in hl_map.values() for frag in frags]
                    results.append(
                        SearchHit(
                            id=str(item["_id"]),
                            index=str(item["_index"]),
                            score=float(item.get("_score") or 0.0),
                            source=item.get("_source", {}),
                            highlights=highlights,
                        )
                    )
                return results
            except Exception as exc:
                logger.warning("Elasticsearch search failed on %s: %s", index, exc)

        # In-memory fallback search (token overlap scoring)
        tokens = [t.lower() for t in query.split() if t.strip()]
        docs = self._memory_indices.get(index, {})
        scored: list[SearchHit] = []
        for doc_id, doc in docs.items():
            if filters and any(str(doc.get(k, "")) != v for k, v in filters.items() if v):
                continue
            haystack = " ".join(str(doc.get(f, "")) for f in fields).lower()
            matches = sum(1 for tok in tokens if tok in haystack)
            if matches > 0 or query.strip() == "*":
                score = float(matches) if matches > 0 else 1.0
                scored.append(
                    SearchHit(
                        id=doc_id,
                        index=index,
                        score=score,
                        source=doc,
                        highlights=[],
                    )
                )
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:size]

    async def summary_counts(self, specs_index: str, events_index: str) -> dict[str, Any]:
        events = list(self._memory_indices.get(events_index, {}).values())
        by_type: dict[str, int] = {}
        for ev in events:
            et = str(ev.get("event_type", "unknown"))
            by_type[et] = by_type.get(et, 0) + 1
        return {
            "backend": self.backend_name,
            "indexed_specs": len(self._memory_indices.get(specs_index, {})),
            "indexed_events": len(events),
            "events_by_type": by_type,
        }
