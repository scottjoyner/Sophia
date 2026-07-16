from voice_agent.util.embed_text import EMBEDDING_FIELD, compute_text_embedding, embedding_payload


def test_embedding_payload_empty_for_blank():
    assert embedding_payload("") == {}
    assert embedding_payload("   ") == {}


def test_embedding_field_name_coordinated_with_contract():
    # Field name must match docs/NEO4J_MEMORY_CONTRACT.md and the unified fleet
    # memory schema (LLD §4.1).
    assert EMBEDDING_FIELD == "embedding"


def test_compute_text_embedding_returns_vector():
    vec = compute_text_embedding("Meeting about roadmap and Q3 planning.")
    assert vec is None or (isinstance(vec, list) and len(vec) > 0 and all(isinstance(x, float) for x in vec))
