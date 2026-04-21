"""
weaviate_client.py — MEGABrain Weaviate collection wrapper.
Uses weaviate-client v4, text2vec-ollama + nomic-embed-text (768-dim).
"""
import json
import uuid
from datetime import datetime
from typing import Optional
import weaviate
import weaviate.classes as wvc

WEAVIATE_URL   = "http://localhost:8080"
COLLECTION     = "MEGABrain"


def _client():
    return weaviate.connect_to_local(host="localhost", port=8080, grpc_port=50051)


def ensure_collection():
    """Create MEGABrain collection if it doesn't exist."""
    client = _client()
    try:
        if client.collections.exists(COLLECTION):
            return
        client.collections.create(
            name=COLLECTION,
            vectorizer_config=wvc.config.Configure.Vectorizer.text2vec_ollama(
                api_endpoint="http://localhost:11434",
                model="nomic-embed-text",
            ),
            properties=[
                wvc.config.Property(name="content",    data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="source",     data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="category",   data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="bot",        data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="created_at", data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="score",      data_type=wvc.config.DataType.NUMBER),
            ],
        )
    finally:
        client.close()


def add_object(content: str, source: str = "", category: str = "general",
               bot: str = "system", score: float = 0.0) -> str:
    """Add a text object to MEGABrain. Returns the UUID."""
    ensure_collection()
    client = _client()
    try:
        col = client.collections.get(COLLECTION)
        obj_id = str(uuid.uuid4())
        col.data.insert(
            properties={
                "content":    content,
                "source":     source,
                "category":   category,
                "bot":        bot,
                "created_at": datetime.utcnow().isoformat(),
                "score":      score,
            },
            uuid=obj_id,
        )
        return obj_id
    finally:
        client.close()


def search(query: str, limit: int = 5, category: Optional[str] = None) -> list[dict]:
    """Semantic search over MEGABrain. Returns list of result dicts."""
    ensure_collection()
    client = _client()
    try:
        col = client.collections.get(COLLECTION)
        filters = None
        if category:
            filters = wvc.query.Filter.by_property("category").equal(category)

        results = col.query.near_text(
            query=query,
            limit=limit,
            filters=filters,
            return_metadata=wvc.query.MetadataQuery(score=True),
        )
        return [
            {
                "uuid":       str(obj.uuid),
                "content":    obj.properties.get("content", ""),
                "source":     obj.properties.get("source", ""),
                "category":   obj.properties.get("category", ""),
                "bot":        obj.properties.get("bot", ""),
                "created_at": obj.properties.get("created_at", ""),
                "score":      obj.properties.get("score", 0.0),
                "certainty":  obj.metadata.score if obj.metadata else None,
            }
            for obj in results.objects
        ]
    finally:
        client.close()


def get_old_objects(days: int = 7, limit: int = 100) -> list[dict]:
    """Return objects older than N days (for re-embedding by Grind)."""
    ensure_collection()
    cutoff = datetime.utcnow().replace(microsecond=0)
    client = _client()
    try:
        col = client.collections.get(COLLECTION)
        results = col.query.fetch_objects(
            limit=limit,
            filters=wvc.query.Filter.by_property("created_at").less_than(
                f"{cutoff.year}-{cutoff.month:02d}-{(cutoff.day - days):02d}T00:00:00"
            ),
        )
        return [
            {"uuid": str(obj.uuid), "content": obj.properties.get("content", "")}
            for obj in results.objects
        ]
    finally:
        client.close()


def count() -> int:
    """Total objects in MEGABrain collection."""
    ensure_collection()
    client = _client()
    try:
        col = client.collections.get(COLLECTION)
        agg = col.aggregate.over_all(total_count=True)
        return agg.total_count or 0
    finally:
        client.close()
