from voice_agent.server.graph_outbox import GraphOutbox, replay_graph_outbox_items


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
    assert outbox.summary()["pending_total"] == 1
    assert outbox.summary()["healthy"] is False


def test_graph_outbox_due_success_and_retry(tmp_path):
    outbox = GraphOutbox(tmp_path / "graph_outbox.sqlite")
    item = outbox.enqueue(
        kind="capture",
        idempotency_key="capture:abc",
        payload={"capture_id": "abc"},
    )

    due = outbox.list_due(limit=10)
    assert [d.id for d in due] == [item.id]
    assert outbox.due_count() == 1

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
    assert outbox.summary()["healthy"] is True


def test_graph_outbox_prunes_old_succeeded_rows(tmp_path):
    outbox = GraphOutbox(tmp_path / "graph_outbox.sqlite")
    item = outbox.enqueue(kind="capture", idempotency_key="capture:old", payload={"capture_id": "old"})
    outbox.mark_succeeded(item.id)

    assert outbox.prune_succeeded(older_than_ms=0, now_ms=outbox.now_ms() + 1) == 1
    assert outbox.get_by_key("capture:old") is None


def test_replay_no_due_items_is_success_without_neo4j_password(tmp_path):
    outbox = GraphOutbox(tmp_path / "graph_outbox.sqlite")

    result = replay_graph_outbox_items(
        outbox,
        neo4j_uri="bolt://example:7687",
        neo4j_user="neo4j",
        neo4j_password="",
    )

    assert result["ok"] is True
    assert result["reason"] == "No due graph outbox items"
    assert result["processed"] == 0
