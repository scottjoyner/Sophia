from __future__ import annotations

from enum import StrEnum

# Local thin shim mirroring the canonical auto-assist contract enum
# (src/assistx/contracts/event_envelope.py :: AuthState). We deliberately do NOT
# add a cross-repo import dependency right now; this mirrors the contract
# fields locally (LLD §1 / W-14). Keep values in sync with the contract.
#
# correlation_id is REQUIRED on every emitted envelope (see assistx_dispatch).


class AuthState(StrEnum):
    AUTHENTICATED_SCOTT = "authenticated_scott"
    UNKNOWN_SPEAKER = "unknown_speaker"
    REGISTERED_USER_UNVERIFIED = "registered_user_unverified"
    ADMIN_VOICE_OVERRIDE = "admin_voice_override"
    REJECTED = "rejected"


# Convenience aliases mirroring the contract member names.
AUTHENTICATED_SCOTT = AuthState.AUTHENTICATED_SCOTT.value
UNKNOWN_SPEAKER = AuthState.UNKNOWN_SPEAKER.value
REGISTERED_USER_UNVERIFIED = AuthState.REGISTERED_USER_UNVERIFIED.value
ADMIN_VOICE_OVERRIDE = AuthState.ADMIN_VOICE_OVERRIDE.value
REJECTED = AuthState.REJECTED.value

SCHEMA_VERSION = "2026-06-08.v1"
