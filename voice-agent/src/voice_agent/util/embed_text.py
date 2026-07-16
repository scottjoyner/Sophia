from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Field name used on graph nodes for vector memory. Coordinated with
# docs/NEO4J_MEMORY_CONTRACT.md and the unified fleet schema (LLD §4.1):
#   SophiaCapture.embedding, Transcript.embedding, MeetingSegment.embedding
EMBEDDING_FIELD = "embedding"


def compute_text_embedding(text: str, *, model: str | None = None) -> list[float] | None:
    """Compute an embedding vector for ``text`` if a local embedder is available.

    Order of preference:
      1. A configured OpenAI-compatible embeddings endpoint
         (SOPHIA_EMBEDDING_* env / config) — used by auto-router normally, but
         available here for graph enrichment.
      2. ``sentence-transformers`` if installed locally.

    Returns ``None`` when no embedder is available. Callers MUST store the
    value under ``EMBEDDING_FIELD`` and treat absence as a TODO (vector index
    populates lazily once an embedder is wired in).

    TODO(W-16): wire a shared fleet embeddings endpoint (auto-router) as the
    canonical vector producer; add the neo4j vector index
    (e.g. ``CREATE VECTOR INDEX capture_embedding ...``) once populated.
    """
    if not text or not text.strip():
        return None

    endpoint = os.getenv("SOPHIA_EMBEDDING_BASE_URL") or os.getenv("EMBEDDING_BASE_URL")
    api_key = os.getenv("SOPHIA_EMBEDDING_API_KEY") or os.getenv("EMBEDDING_API_KEY")
    embedding_model = model or os.getenv("SOPHIA_EMBEDDING_MODEL") or os.getenv("EMBEDDING_MODEL")
    if endpoint and embedding_model:
        try:
            import httpx

            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            resp = httpx.post(
                endpoint.rstrip("/") + "/embeddings",
                headers=headers,
                json={"input": text, "model": embedding_model},
                timeout=20.0,
            )
            resp.raise_for_status()
            data = resp.json()
            vec = (data.get("data") or [{}])[0].get("embedding")
            if vec:
                return [float(x) for x in vec]
        except Exception as exc:  # pragma: no cover - optional path
            logger.debug("embeddings endpoint unavailable: %s", exc)

    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        local_model = os.getenv("SOPHIA_LOCAL_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
        model_obj = SentenceTransformer(local_model)
        vec = model_obj.encode(text, normalize_embeddings=True)
        return [float(x) for x in vec]
    except Exception as exc:  # sentence-transformers optional; leave TODO
        logger.debug("local sentence-transformers unavailable: %s", exc)

    # TODO(W-16): no embedder available — leave schema field empty so the
    # vector index can be backfilled later.
    return None


def embedding_payload(text: str, *, model: str | None = None) -> dict[str, Any]:
    """Return ``{EMBEDDING_FIELD: [...] }`` or ``{}`` when unavailable."""
    vec = compute_text_embedding(text, model=model)
    if vec is None:
        return {}
    return {EMBEDDING_FIELD: vec}
