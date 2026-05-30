from voice_agent.auth.neo4j_schema import SOPHIA_SCHEMA_QUERIES


def test_sophia_schema_contains_idempotency_constraints():
    joined = "\n".join(SOPHIA_SCHEMA_QUERIES)

    assert "SophiaCapture" in joined
    assert "dedupe_key IS UNIQUE" in joined
    assert "capture_id IS UNIQUE" in joined
    assert "Transcript" in joined
    assert "id IS UNIQUE" in joined
    assert "Speaker" in joined
    assert "user_id IS UNIQUE" in joined
    assert "Audio" in joined
    assert "path IS UNIQUE" in joined
    assert "Device" in joined
    assert "Meeting" in joined
    assert "IF NOT EXISTS" in joined
