from voice_agent.server.trusted_sessions import TrustedSessionStore


def test_trusted_session_lifecycle(tmp_path):
    store = TrustedSessionStore(tmp_path / "sessions.sqlite", ttl_ms=60_000)

    created = store.upsert(
        user_id="scott",
        session_id="mobile",
        device_id="iphone",
        device_fingerprint="fp",
        score=0.91,
        accepted=True,
        match_source="active_head",
        voiceprint_version_id="vp1",
    )

    assert created.accepted is True
    assert created.score == 0.91
    assert created.session_key

    loaded = store.get(
        user_id="scott",
        session_id="mobile",
        device_id="iphone",
        device_fingerprint="fp",
    )
    assert loaded is not None
    assert loaded.user_id == "scott"
    assert loaded.session_id == "mobile"
    assert loaded.device_id == "iphone"
    assert loaded.match_source == "active_head"

    assert store.clear(
        user_id="scott",
        session_id="mobile",
        device_id="iphone",
        device_fingerprint="fp",
    ) is True
    assert store.get(
        user_id="scott",
        session_id="mobile",
        device_id="iphone",
        device_fingerprint="fp",
    ) is None


def test_expired_trusted_session_is_removed(tmp_path):
    store = TrustedSessionStore(tmp_path / "sessions.sqlite", ttl_ms=-1)
    store.upsert(user_id="scott", session_id="mobile", score=0.9, accepted=True)

    assert store.get(user_id="scott", session_id="mobile") is None
    assert store.prune_expired() >= 0
