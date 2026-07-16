from __future__ import annotations

# Local thin shim that re-exports the canonical auto-assist contract types from
# ``assistx.contracts.event_envelope`` when the package is importable (e.g. when
# auto-assist is on sys.path as a submodule/path). Otherwise it falls back to a
# local mirror so standalone runs keep working without the canonical package.
#
# Canonical source of truth:
#   /media/scott/SSD_4TB/hermes-home/home_scott_git_auto-assist/src/assistx/contracts/
# See docs/LLD_UNIFIED_FLEET.md §1 and §2 (Trace Observability / G1).
#
# correlation_id is REQUIRED on every emitted envelope (see assistx_dispatch).

try:
    from assistx.contracts.event_envelope import (
        Actor,
        AuthState,
        EventEnvelope as EventEnvelop,
        EventLink,
        TraceEvent,
        TraceGroup,
    )
    from assistx.contracts.version import SCHEMA_VERSION

    _USING_CANONICAL = True
except ImportError:
    from enum import StrEnum

    class AuthState(StrEnum):
        AUTHENTICATED_SCOTT = "authenticated_scott"
        UNKNOWN_SPEAKER = "unknown_speaker"
        REGISTERED_USER_UNVERIFIED = "registered_user_unverified"
        ADMIN_VOICE_OVERRIDE = "admin_voice_override"
        REJECTED = "rejected"

    class Actor:  # pragma: no cover - fallback only
        pass

    class EventLink:  # pragma: no cover - fallback only
        pass

    class EventEnvelop:  # pragma: no cover - fallback only
        pass

    class TraceEvent:  # pragma: no cover - fallback only
        pass

    class TraceGroup:  # pragma: no cover - fallback only
        pass

    SCHEMA_VERSION = "2026-06-08.v1"

    _USING_CANONICAL = False


# Convenience aliases mirroring the contract member names (value strings).
AUTHENTICATED_SCOTT = AuthState.AUTHENTICATED_SCOTT.value
UNKNOWN_SPEAKER = AuthState.UNKNOWN_SPEAKER.value
REGISTERED_USER_UNVERIFIED = AuthState.REGISTERED_USER_UNVERIFIED.value
ADMIN_VOICE_OVERRIDE = AuthState.ADMIN_VOICE_OVERRIDE.value
REJECTED = AuthState.REJECTED.value


__all__ = [
    "AuthState",
    "Actor",
    "EventEnvelop",
    "EventLink",
    "TraceEvent",
    "TraceGroup",
    "SCHEMA_VERSION",
    "AUTHENTICATED_SCOTT",
    "UNKNOWN_SPEAKER",
    "REGISTERED_USER_UNVERIFIED",
    "ADMIN_VOICE_OVERRIDE",
    "REJECTED",
    "_USING_CANONICAL",
]
