from voice_agent.server.graph_outbox import GraphOutbox


def test_graph_outbox_enqueue_dedupes_by_idempotency_key(tmp_path):
    outbox = GraphOutbox(tmp_path / "graph_outbox.sqlite")

    first = outbox.enqueue(
        kind="capture",
        idempotency_key="capture:abc",
        payload={"capture_id": "abc", "transcript": "hello"},
        error="graph down",
    )
    second = outbox.enqueue(
        kind="capture",
        idempotency_key="capture:abc",
        payload={"capture_id": "abc", "transcript": "updated"},
        error="still down",
    )

    assert first.idempotency_key == second.idempotency_key
    assert second.payload["transcript"] == "updated"
    assert outbox.counts()["pending"] == 1


def test_graph_outbox_due_success_and_retry(tmp_path):
    outbox = GraphOutbox(tmp_path / "graph_outbox.sqlite")
    item = outbox.enqueue(
        kind="capture",
        idempotency_key="capture:abc",
        payload={"capture_id": "abc"},
    )

    due = outbox.list_due(limit=10)
    assert [d.id for d in due] == [item.id]

    outbox.mark_failed(item.id, "neo4j timeout", base_delay_ms=1_000)
    retry_item = outbox.get_by_key("capture:abc")
    assert retry_item is not None
    assert retry_item.status == "retry"
    assert retry_item.attempts == 1
    assert retry_item.last_error == "neo4j timeout"
    assert outbox.list_due(now_ms=retry_item.next_attempt_ms - 1) == []
    assert outbox.list_due(now_ms=retry_item.next_attempt_ms)[0].id == item.id

    outbox.mark_succeeded(item.id)
    done = outbox.get_by_key("capture:abc")
    assert done is not None
    assert done.status == "succeeded"
    assert outbox.counts()["succeeded"] == 1
