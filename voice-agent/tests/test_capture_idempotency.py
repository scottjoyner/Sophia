from voice_agent.server.capture_idempotency import CaptureIdempotencyStore


def test_capture_idempotency_store_replays_response(tmp_path):
    store = CaptureIdempotencyStore(tmp_path / "idempotency.sqlite")
    response = {"ok": True, "capture_id": "server-1", "graph_saved": True}

    store.put("client-1", "server-1", response)
    replay = store.get("client-1")

    assert replay is not None
    assert replay["ok"] is True
    assert replay["capture_id"] == "server-1"
    assert replay["client_capture_id"] == "client-1"
    assert replay["idempotent_replay"] is True


def test_capture_idempotency_ignores_empty_key(tmp_path):
    store = CaptureIdempotencyStore(tmp_path / "idempotency.sqlite")
    store.put("", "server-1", {"ok": True})
    assert store.get("") is None


def test_capture_idempotency_summary_counts_active_and_expired(tmp_path):
    active = CaptureIdempotencyStore(tmp_path / "active.sqlite")
    active.put("client-1", "server-1", {"ok": True, "capture_id": "server-1"})
    summary = active.summary()
    assert summary["counts"]["total"] == 1
    assert summary["counts"]["active"] == 1
    assert summary["counts"]["expired"] == 0
    assert summary["healthy"] is True

    expired = CaptureIdempotencyStore(tmp_path / "expired.sqlite", ttl_ms=-1)
    expired.put("client-2", "server-2", {"ok": True, "capture_id": "server-2"})
    expired_summary = expired.summary()
    assert expired_summary["counts"]["total"] == 1
    assert expired_summary["counts"]["expired"] == 1
    assert expired_summary["healthy"] is False


def test_capture_idempotency_expired_entry_is_removed(tmp_path):
    store = CaptureIdempotencyStore(tmp_path / "idempotency.sqlite", ttl_ms=-1)
    store.put("client-1", "server-1", {"ok": True, "capture_id": "server-1"})

    assert store.get("client-1") is None
    assert store.prune_expired() >= 0
