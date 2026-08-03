from voice_agent.contracts_shim import (
    ADMIN_VOICE_OVERRIDE,
    AUTHENTICATED_SCOTT,
    REGISTERED_USER_UNVERIFIED,
    REJECTED,
    UNKNOWN_SPEAKER,
    AuthState,
)
from voice_agent.server.assistx_dispatch import (
    assistx_voice_event_payload,
    build_voice_event,
)


def test_contract_auth_state_values_match_canonical_enum():
    assert AuthState.AUTHENTICATED_SCOTT.value == "authenticated_scott"
    assert AuthState.UNKNOWN_SPEAKER.value == "unknown_speaker"
    assert AuthState.REGISTERED_USER_UNVERIFIED.value == "registered_user_unverified"
    assert AuthState.ADMIN_VOICE_OVERRIDE.value == "admin_voice_override"
    assert AuthState.REJECTED.value == "rejected"


def test_build_voice_event_emits_full_auth_state_taxonomy():
    # Owner scott with accepted -> authenticated_scott
    p = build_voice_event("task_created", text="hi", actor={"user_id": "scott", "device_id": "d1"}, metadata={"accepted": True})
    assert p["actor"]["auth_state"] == AUTHENTICATED_SCOTT
    assert p["actor"]["user_id"] == "scott"
    assert p["event_type"] == "task_created"
    assert p["auto_dispatch"] is True

    # Unknown user_id -> registered_user_unverified and review-only event.
    p = build_voice_event("task_created", actor={"user_id": "alice"}, metadata={})
    assert p["actor"]["auth_state"] == REGISTERED_USER_UNVERIFIED
    assert p["event_type"] == "task_proposed"
    assert p["auto_dispatch"] is False
    assert p["metadata"]["authorization_action"] == "review_required"

    # No user -> unknown_speaker. Never default identity to Scott.
    p = build_voice_event("task_created", actor={}, metadata={})
    assert p["actor"]["auth_state"] == UNKNOWN_SPEAKER
    assert p["actor"]["user_id"] == "unknown"
    assert p["event_type"] == "task_proposed"
    assert p["auto_dispatch"] is False

    # Explicit rejected requests remain auditable but cannot create tasks.
    p = build_voice_event("task_created", actor={"user_id": "scott", "auth_state": REJECTED}, metadata={})
    assert p["actor"]["auth_state"] == REJECTED
    assert p["event_type"] == "voice_action_rejected"
    assert p["auto_dispatch"] is False
    assert p["metadata"]["authorization_action"] == "rejected"

    # Explicit values pass through unchanged.
    p = build_voice_event("voice_auth", actor={"user_id": "x", "auth_state": UNKNOWN_SPEAKER}, metadata={})
    assert p["actor"]["auth_state"] == UNKNOWN_SPEAKER

    # Explicit admin override is trusted by the contract boundary.
    p = build_voice_event("task_created", actor={"auth_state": ADMIN_VOICE_OVERRIDE}, metadata={})
    assert p["actor"]["auth_state"] == ADMIN_VOICE_OVERRIDE
    assert p["actor"]["user_id"] == "scott"
    assert p["event_type"] == "task_created"
    assert p["auto_dispatch"] is True


def test_build_voice_event_requires_correlation_id():
    p = build_voice_event("task_created", text="hi")
    # correlation_id must be present (contract enforcement, LLD §2).
    assert p["correlation_id"]
    assert p["schema_version"] == "2026-06-08.v1"


def test_wire_payload_preserves_identity_and_trace_in_metadata():
    local = build_voice_event(
        "task_created",
        text="restart the service",
        actor={"user_id": "scott", "device_id": "phone", "auth_state": AUTHENTICATED_SCOTT},
    )
    wire = assistx_voice_event_payload(local)

    assert wire["event_type"] == "task_created"
    assert wire["metadata"]["auth_state"] == AUTHENTICATED_SCOTT
    assert wire["metadata"]["user_id"] == "scott"
    assert wire["metadata"]["device_id"] == "phone"
    assert wire["metadata"]["correlation_id"] == local["correlation_id"]
    assert wire["metadata"]["links"] == local["links"]
    assert "actor" not in wire
    assert "links" not in wire
    assert "schema_version" not in wire
    assert "correlation_id" not in wire
