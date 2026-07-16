from voice_agent.contracts_shim import (
    AUTHENTICATED_SCOTT,
    REGISTERED_USER_UNVERIFIED,
    REJECTED,
    UNKNOWN_SPEAKER,
    AuthState,
)
from voice_agent.server.assistx_dispatch import build_voice_event


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

    # Unknown user_id -> registered_user_unverified
    p = build_voice_event("task_created", actor={"user_id": "alice"}, metadata={})
    assert p["actor"]["auth_state"] == REGISTERED_USER_UNVERIFIED

    # No user -> unknown_speaker
    p = build_voice_event("task_created", actor={}, metadata={})
    assert p["actor"]["auth_state"] == UNKNOWN_SPEAKER

    # Explicit rejected
    p = build_voice_event("task_created", actor={"user_id": "scott", "auth_state": REJECTED}, metadata={})
    assert p["actor"]["auth_state"] == REJECTED

    # Explicit values pass through unchanged
    p = build_voice_event("task_created", actor={"user_id": "x", "auth_state": UNKNOWN_SPEAKER}, metadata={})
    assert p["actor"]["auth_state"] == UNKNOWN_SPEAKER


def test_build_voice_event_requires_correlation_id():
    p = build_voice_event("task_created", text="hi")
    # correlation_id must be present (contract enforcement, LLD §2).
    assert p["correlation_id"]
    assert p["schema_version"] == "2026-06-08.v1"
