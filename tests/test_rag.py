from pathlib import Path

from app.rag import DriveRAGStore, _chunk_text


def test_chunk_text_handles_empty_and_overlap():
    assert _chunk_text("   ") == []
    assert _chunk_text("short doc", chunk_size=100, overlap=20) == ["short doc"]

    long_text = "A" * 1000
    chunks = _chunk_text(long_text, chunk_size=400, overlap=100)
    assert len(chunks) == 3
    assert len(chunks[0]) == 400


def test_rag_upsert_and_semantic_search(tmp_path: Path):
    store = DriveRAGStore(
        persist_dir=str(tmp_path / "chroma_unit"),
        collection_name="unit_specs",
    )

    chunks_count = store.upsert_document(
        doc_id="spec_100",
        title="HITL_Security_SPEC.md",
        project="ops-crew",
        content="All write actions must pass through the SQLAlchemy HITL Approval Gate before execution.",
    )
    assert chunks_count == 1

    store.upsert_document(
        doc_id="spec_200",
        title="Frontend_Theme.md",
        project="ui-kit",
        content="Liquid Glass buttons use backdrop blur and subtle neon borders.",
    )

    results = store.search(query="HITL approval gate write actions", top_k=2)
    assert len(results) >= 1
    assert results[0].title == "HITL_Security_SPEC.md"

    filtered = store.search(query="buttons", top_k=2, project="ops-crew")
    assert all(r.project == "ops-crew" for r in filtered)


def test_seed_sample_specs_is_idempotent(tmp_path: Path):
    store = DriveRAGStore(
        persist_dir=str(tmp_path / "chroma_seed"),
        collection_name="seed_specs",
    )
    store.seed_sample_specs_if_empty()
    first_count = store._collection.count()
    assert first_count >= 2

    store.seed_sample_specs_if_empty()
    assert store._collection.count() == first_count
