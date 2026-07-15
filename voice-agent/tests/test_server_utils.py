from __future__ import annotations

import asyncio
import io
import time

from starlette.datastructures import UploadFile

from voice_agent.server.auth_session import (
    create_session_token,
    login,
    verify_session_token,
)
from voice_agent.server.events import EventBus, EventRecord, event_to_dict
from voice_agent.server.upload_limits import (
    CAPTURE_AUDIO_POLICY,
    UploadPolicy,
    read_upload_with_limits,
    safe_upload_suffix,
)


def test_event_bus_publish_and_snapshot() -> None:
    bus = EventBus()
    bus.publish("a", {"x": 1})
    bus.publish("b", {"session_id": "s1", "y": 2})
    assert len(bus.snapshot()) == 2
    assert len(bus.snapshot(after_id=1)) == 1
    sess = bus.snapshot(session_id="s1")
    assert len(sess) == 1 and sess[0].type == "b"
    assert event_to_dict(sess[0])["type"] == "b"


def test_event_bus_subscribe_fanout() -> None:
    bus = EventBus()
    queue = bus.subscribe()
    rec = bus.publish("evt", {"v": 1})
    got = queue.get_nowait()
    assert got.id == rec.id
    bus.unsubscribe(queue)
    assert queue not in bus._subscribers


def test_event_record_dataclass() -> None:
    rec = EventRecord(id=1, type="t", payload={"a": 1})
    assert rec.id == 1 and rec.payload["a"] == 1


def test_session_token_roundtrip_and_rejects() -> None:
    token = create_session_token()
    assert verify_session_token(token) is True
    assert verify_session_token("bad.token") is False
    assert verify_session_token(None) is False
    # Expired token
    expired = token.split(".")[0] + f".{int(time.time()) - 1000}" + "." + "x"
    assert verify_session_token(expired) is False


def test_login_valid_and_invalid() -> None:
    import os

    os.environ["SOPHIA_APP_PASSWORD"] = "secret-pass"
    try:
        assert login("secret-pass") is not None
        assert login("wrong") is None
    finally:
        del os.environ["SOPHIA_APP_PASSWORD"]


def test_upload_policy_build_normalizes_suffixes() -> None:
    policy = UploadPolicy.build("test", 100, ["WAV", "mp3"])
    assert ".wav" in policy.allowed_suffixes
    assert ".mp3" in policy.allowed_suffixes
    assert policy.max_bytes == 100


def test_safe_upload_suffix_policy_rejects_unsupported() -> None:
    good = UploadFile(filename="clip.wav", file=io.BytesIO(b"x"))
    assert safe_upload_suffix(good, policy=CAPTURE_AUDIO_POLICY) == ".wav"
    bad = UploadFile(filename="clip.exe", file=io.BytesIO(b"x"))
    try:
        safe_upload_suffix(bad, policy=CAPTURE_AUDIO_POLICY)
        assert False, "expected 415"
    except Exception as exc:
        assert exc.status_code == 415


async def test_read_upload_with_limits() -> None:
    empty = UploadFile(filename="a.wav", file=io.BytesIO(b""))
    try:
        await read_upload_with_limits(empty, CAPTURE_AUDIO_POLICY)
        assert False, "expected 400"
    except Exception as exc:
        assert exc.status_code == 400

    big = UploadFile(filename="a.wav", file=io.BytesIO(b"x" * (CAPTURE_AUDIO_POLICY.max_bytes + 1)))
    try:
        await read_upload_with_limits(big, CAPTURE_AUDIO_POLICY)
        assert False, "expected 413"
    except Exception as exc:
        assert exc.status_code == 413

    ok = UploadFile(filename="a.wav", file=io.BytesIO(b"hello"))
    data = await read_upload_with_limits(ok, CAPTURE_AUDIO_POLICY)
    assert data == b"hello"
