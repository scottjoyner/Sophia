from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from collections.abc import Callable
from typing import Any

from ...util.time import now_ms as current_time_ms
from .store import RelayStore


class RelayError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class RelayBroker:
    """Authoritative broker for Tommy relay device/session leases."""

    def __init__(
        self,
        store: RelayStore,
        lease_ttl_ms: int = 15_000,
        *,
        device_enrollment_token: str | None = None,
        event_hook: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.store = store
        self.lease_ttl_ms = lease_ttl_ms
        self.device_enrollment_token = device_enrollment_token or os.getenv("TOMMY_RELAY_ENROLLMENT_TOKEN") or os.getenv("SOPHIA_RELAY_ENROLLMENT_TOKEN")
        self.event_hook = event_hook

    def register_device(
        self,
        device_id: str,
        name: str = "",
        owner_id: str = "scott",
        capabilities: list[str] | None = None,
        platform: str = "",
        mesh_node: str = "",
        now_ms: int | None = None,
        enrollment_token: str | None = None,
        device_token: str | None = None,
        fallback_priority: int = 100,
        trusted: bool = False,
        audio_source: str = "",
        tailscale_ip: str = "",
        location: str = "",
    ) -> dict[str, Any]:
        now = self._now(now_ms)
        existing = self.store.get_device(device_id)
        token = None
        token_hash = existing.get("token_hash") if existing else None
        enrollment_authorized = bool(
            self.device_enrollment_token
            and hmac.compare_digest(enrollment_token or "", self.device_enrollment_token)
        )
        if not existing or not token_hash:
            if self.device_enrollment_token and not enrollment_authorized:
                raise RelayError("Invalid relay device enrollment token", 401)
            token = self._device_token()
            token_hash = self._hash_secret(token)
        elif not (self._secret_matches(device_token, token_hash) or enrollment_authorized):
            raise RelayError("Existing relay device refresh requires device token or enrollment token", 401)
        trusted_value = bool((existing or {}).get("trusted", False) or (trusted and enrollment_authorized))
        previous = existing or {}
        audio_source = audio_source or previous.get("audio_source", "")
        tailscale_ip = tailscale_ip or previous.get("tailscale_ip", "")
        location = location or previous.get("location", "")
        device = self.store.upsert_device(
            {
                "device_id": device_id,
                "name": name,
                "owner_id": owner_id,
                "capabilities": capabilities or [],
                "platform": platform,
                "mesh_node": mesh_node,
                "status": "online",
                "last_seen_ms": now,
                "token_hash": token_hash,
                "fallback_priority": fallback_priority if trusted_value else max(fallback_priority, 1000),
                "trusted": trusted_value,
                "audio_source": audio_source,
                "tailscale_ip": tailscale_ip,
                "location": location,
            }
        )
        payload = {"device_id": device_id, "owner_id": owner_id, "ts_ms": now}
        self._event("relay_device_registered", payload)
        result = {"ok": True, **self._public_device(device)}
        if token:
            result["device_token"] = token
        return result

    def attach(
        self,
        session_id: str,
        device_id: str,
        owner_id: str = "scott",
        now_ms: int | None = None,
        *,
        device_token: str | None = None,
        resume_token: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        now = self._now(now_ms)
        self._require_device_auth(device_id, device_token)
        session = self.store.get_session(session_id)
        lease = self.store.get_lease(session_id)
        if session and resume_token and self._secret_matches(resume_token, session.get("resume_token_hash")):
            return self.resume(session_id, device_id, resume_token, device_token=device_token, now_ms=now)
        active_device = session.get("active_device_id") if session else None
        lease_active = bool(lease and lease.get("revoked_at_ms") is None and lease["lease_expires_at_ms"] > now)
        if active_device and lease_active and active_device != device_id and not force:
            raise RelayError("Session is already active; use handoff or force_handoff", 409)
        if active_device and active_device != device_id and force:
            self.store.revoke_lease(session_id, now_ms=now, reason="force_attach")
            self._event("relay_lease_revoked", {"session_id": session_id, "device_id": active_device, "reason": "force_attach", "ts_ms": now})
        return self._grant(session_id, device_id, owner_id, now, state="listening", event_type="relay_session_attached")

    def resume(
        self,
        session_id: str,
        device_id: str,
        resume_token: str,
        *,
        device_token: str | None = None,
        now_ms: int | None = None,
        last_seen_event_id: int = 0,
        last_seen_seq: int | None = None,
    ) -> dict[str, Any]:
        now = self._now(now_ms)
        self._require_device_auth(device_id, device_token)
        session = self.store.get_session(session_id)
        if not session:
            raise RelayError(f"Unknown session: {session_id}", 404)
        if not self._secret_matches(resume_token, session.get("resume_token_hash")):
            raise RelayError("Invalid resume token", 401)
        result = self._grant(session_id, device_id, session.get("owner_id", "scott"), now, state="listening", event_type="relay_session_resumed")
        result["replay"] = {
            "events": self.store.list_events(after_id=last_seen_event_id, session_id=session_id),
            "transcripts": self.store.list_transcripts(session_id, after_seq=last_seen_seq),
            "next_expected_seq": result.get("expected_seq"),
        }
        return result

    def heartbeat(
        self,
        device_id: str,
        session_id: str | None = None,
        lease_token: str | None = None,
        seq: int | None = None,
        now_ms: int | None = None,
        *,
        device_token: str | None = None,
    ) -> dict[str, Any]:
        now = self._now(now_ms)
        device = self._require_device_auth(device_id, device_token)
        self.store.upsert_device({**device, "last_seen_ms": now, "status": "online"})
        active = False
        expires = None
        if session_id and lease_token:
            lease = self.store.get_lease(session_id)
            if lease and lease["device_id"] == device_id and self._lease_matches(lease, lease_token) and not lease.get("revoked_at_ms"):
                if lease["lease_expires_at_ms"] > now:
                    active = True
                    expires = now + self.lease_ttl_ms
                    self.store.save_lease({**lease, "lease_expires_at_ms": expires, "updated_at_ms": now, "revoked_at_ms": None, "revoked_reason": None})
                    if seq is not None:
                        session = self.store.get_session(session_id)
                        if session:
                            self.store.upsert_session({**session, "last_seq": seq, "expected_seq": max(seq + 1, int(session.get("expected_seq") or 0)), "updated_at_ms": now})
                else:
                    self.expire_session(session_id, now_ms=now)
        payload = {"ok": True, "device_id": device_id, "session_id": session_id, "active": active, "lease_expires_at_ms": expires, "ts_ms": now}
        self._event("relay_device_heartbeat", payload)
        return payload

    def validate_active_lease(
        self,
        session_id: str,
        device_id: str,
        lease_token: str,
        *,
        device_token: str | None = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        now = self._now(now_ms)
        self._require_device_auth(device_id, device_token)
        self._require_active_lease(session_id, device_id, lease_token, now)
        return {"ok": True, "session_id": session_id, "device_id": device_id, "active": True, "ts_ms": now}

    def handoff(
        self,
        session_id: str,
        from_device_id: str | None,
        to_device_id: str,
        reason: str = "manual",
        now_ms: int | None = None,
        *,
        lease_token: str | None = None,
        device_token: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        now = self._now(now_ms)
        if force and not device_token:
            self._require_trusted_force_target(to_device_id)
        else:
            self._require_device_auth(to_device_id, device_token)
        session = self.store.get_session(session_id)
        if not session:
            raise RelayError(f"Unknown session: {session_id}", 404)
        current = session.get("active_device_id")
        if not force:
            if not lease_token:
                raise RelayError("Current lease token required for handoff", 409)
            if from_device_id and current and current != from_device_id:
                raise RelayError("from_device_id does not own the active session", 409)
            if not current:
                raise RelayError("No active session owner for handoff", 409)
            self._require_active_lease(session_id, current, lease_token, now)
        if current and current != to_device_id:
            self.store.revoke_lease(session_id, now_ms=now, reason=reason)
            self._event("relay_lease_revoked", {"session_id": session_id, "device_id": current, "to_device_id": to_device_id, "reason": reason, "ts_ms": now})
        result = self._grant(session_id, to_device_id, session.get("owner_id", "scott"), now, state="listening", event_type="relay_session_handoff")
        result.update({"from_device_id": current, "reason": reason})
        return result

    def detach(self, session_id: str, device_id: str, lease_token: str | None = None, now_ms: int | None = None, *, device_token: str | None = None) -> dict[str, Any]:
        now = self._now(now_ms)
        self._require_device_auth(device_id, device_token)
        if not lease_token:
            raise RelayError("Active lease token required for detach", 409)
        self._require_active_lease(session_id, device_id, lease_token, now)
        lease = self.store.get_lease(session_id)
        session = self.store.get_session(session_id) or {"session_id": session_id, "owner_id": "scott", "last_seq": None}
        self.store.revoke_lease(session_id, now_ms=now, reason="detach")
        self.store.upsert_session({**session, "active_device_id": None, "state": "parked", "updated_at_ms": now})
        self._event("relay_session_detached", {"session_id": session_id, "device_id": device_id, "ts_ms": now})
        return {"ok": True, "session_id": session_id, "active_device_id": None, "state": "parked", "lease": self._public_lease(lease)}

    def expire_session(self, session_id: str, now_ms: int | None = None) -> dict[str, Any]:
        now = self._now(now_ms)
        session = self.store.get_session(session_id)
        if not session:
            raise RelayError(f"Unknown session: {session_id}", 404)
        lease = self.store.get_lease(session_id)
        old_device = session.get("active_device_id")
        self.store.revoke_lease(session_id, now_ms=now, reason="expired")
        candidates = self.store.candidate_fallback_devices(owner_id=session.get("owner_id", "scott"), exclude_device_id=old_device, now_ms=now)
        if candidates:
            candidate = candidates[0]
            self._event("relay_fallback_offered", {"session_id": session_id, "device_id": candidate["device_id"], "from_device_id": old_device, "ts_ms": now})
            return self.handoff(session_id, old_device, candidate["device_id"], reason="fallback_promotion", now_ms=now, force=True)
        updated = self.store.upsert_session({**session, "active_device_id": None, "state": "expired", "updated_at_ms": now})
        self._event("relay_session_expired", {"session_id": session_id, "device_id": old_device, "ts_ms": now})
        return {"ok": True, **self._public_session(updated), "lease": self._public_lease(lease)}

    def session_status(self, session_id: str, now_ms: int | None = None) -> dict[str, Any]:
        now = self._now(now_ms)
        session = self.store.get_session(session_id)
        if not session:
            raise RelayError(f"Unknown session: {session_id}", 404)
        lease = self.store.get_lease(session_id)
        if lease and lease.get("revoked_at_ms") is None and lease["lease_expires_at_ms"] <= now:
            promoted = self.expire_session(session_id, now_ms=now)
            promoted["lease"] = {**(self._public_lease(lease) or {}), "expired": True}
            return promoted
        active_device = self.store.get_device(session.get("active_device_id")) if session.get("active_device_id") else None
        lease_public = self._public_lease(lease) if lease else None
        ttl_remaining = None
        if lease and lease.get("revoked_at_ms") is None:
            ttl_remaining = max(0, int(lease["lease_expires_at_ms"]) - now)
        return {
            "ok": True,
            **self._public_session(session),
            "lease": lease_public,
            "lease_ttl_ms_remaining": ttl_remaining,
            "active_device": self._public_device(active_device) if active_device else None,
            "fallback_candidates": [self._public_device(device) for device in self.store.candidate_fallback_devices(owner_id=session.get("owner_id", "scott"), exclude_device_id=session.get("active_device_id"), now_ms=now)],
        }

    def list_sessions(self) -> dict[str, Any]:
        return {"ok": True, "sessions": [self._public_session(session) for session in self.store.list_sessions()]}

    def list_devices(self) -> dict[str, Any]:
        return {"ok": True, "devices": [self._public_device(device) for device in self.store.list_devices()]}

    def set_device_trust(self, device_id: str, *, trusted: bool, now_ms: int | None = None) -> dict[str, Any]:
        now = self._now(now_ms)
        self._require_device(device_id)
        device = self.store.update_device_fields(device_id, {"trusted": 1 if trusted else 0}, now_ms=now)
        if not device:
            raise RelayError(f"Unknown device: {device_id}", 404)
        payload = {"device_id": device_id, "trusted": trusted, "ts_ms": now}
        self._event("relay_device_trust_updated", payload)
        return {"ok": True, **self._public_device(device)}

    def rotate_device_token(self, device_id: str, *, now_ms: int | None = None) -> dict[str, Any]:
        now = self._now(now_ms)
        self._require_device(device_id)
        token = self._device_token()
        device = self.store.update_device_fields(device_id, {"token_hash": self._hash_secret(token), "status": "online"}, now_ms=now)
        if not device:
            raise RelayError(f"Unknown device: {device_id}", 404)
        self._event("relay_device_token_rotated", {"device_id": device_id, "ts_ms": now})
        return {"ok": True, **self._public_device(device), "device_token": token}

    def revoke_device(self, device_id: str, *, now_ms: int | None = None, reason: str = "revoked") -> dict[str, Any]:
        now = self._now(now_ms)
        self._require_device(device_id)
        device = self.store.update_device_fields(device_id, {"status": "revoked", "trusted": 0}, now_ms=now)
        if not device:
            raise RelayError(f"Unknown device: {device_id}", 404)
        for session in self.store.list_sessions():
            if session.get("active_device_id") == device_id:
                self.store.revoke_lease(session["session_id"], now_ms=now, reason=f"device_{reason}")
                self.store.upsert_session({**session, "active_device_id": None, "state": "parked", "updated_at_ms": now})
                self._event("relay_lease_revoked", {"session_id": session["session_id"], "device_id": device_id, "reason": reason, "ts_ms": now})
        self._event("relay_device_revoked", {"device_id": device_id, "reason": reason, "ts_ms": now})
        return {"ok": True, **self._public_device(device), "reason": reason}

    def webrtc_offer(self, session_id: str, device_id: str, sdp: str, offer_type: str = "offer", now_ms: int | None = None, *, lease_token: str | None = None, device_token: str | None = None) -> dict[str, Any]:
        now = self._now(now_ms)
        self._require_device_auth(device_id, device_token)
        if not lease_token:
            raise RelayError("Active lease token required for WebRTC negotiation", 409)
        self._require_active_lease(session_id, device_id, lease_token, now)
        self._event("relay_webrtc_offer_received", {"session_id": session_id, "device_id": device_id, "type": offer_type, "ts_ms": now})
        return {
            "ok": True,
            "session_id": session_id,
            "device_id": device_id,
            "transport": "webrtc",
            "status": "not_configured",
            "type": "answer",
            "sdp": "",
            "ice_servers": [],
            "fallback": {"type": "websocket", "endpoint": f"/relay/sessions/{session_id}/stream"},
        }

    def record_audio_chunk(self, session_id: str, device_id: str, lease_token: str, seq: int, encoding: str, byte_count: int, now_ms: int | None = None, *, device_token: str | None = None) -> dict[str, Any]:
        now = self._now(now_ms)
        self._require_device_auth(device_id, device_token)
        self._require_active_lease(session_id, device_id, lease_token, now)
        session = self.store.get_session(session_id) or {"expected_seq": 0, "missing_ranges": []}
        expected = int(session.get("expected_seq") or 0)
        missing_ranges = list(session.get("missing_ranges") or [])
        gap = None
        if seq > expected:
            gap = [expected, seq - 1]
            missing_ranges.append(gap)
            self._event("relay_audio_gap", {"session_id": session_id, "device_id": device_id, "expected_seq": expected, "received_seq": seq, "missing_range": gap, "ts_ms": now})
        elif seq < expected:
            missing_ranges = self._close_missing_seq(missing_ranges, seq)
        new_expected = max(expected, seq + 1)
        accepted = self.store.record_audio_chunk(
            {"session_id": session_id, "device_id": device_id, "seq": seq, "encoding": encoding, "byte_count": byte_count, "ts_ms": now},
            expected_seq=new_expected,
            missing_ranges=missing_ranges,
        )
        if not accepted:
            return {"ok": True, "accepted": False, "reason": "duplicate_sequence", "seq": seq}
        payload = {"session_id": session_id, "device_id": device_id, "seq": seq, "encoding": encoding, "bytes": byte_count, "gap": gap, "next_expected_seq": new_expected, "ts_ms": now}
        self._event("relay_audio_chunk", payload)
        return {"ok": True, "accepted": True, **payload}

    def record_transcript(self, session_id: str, device_id: str, lease_token: str, seq: int, text: str, partial: bool = False, source: str = "stt", metadata: dict[str, Any] | None = None, now_ms: int | None = None, *, device_token: str | None = None) -> dict[str, Any]:
        now = self._now(now_ms)
        self._require_device_auth(device_id, device_token)
        self._require_active_lease(session_id, device_id, lease_token, now)
        transcript_id = self.store.record_transcript({"session_id": session_id, "device_id": device_id, "seq": seq, "text": text, "partial": partial, "source": source, "metadata": metadata or {}, "ts_ms": now, "queued_at_ms": now if text and not partial else None})
        accepted = transcript_id is not None
        payload = {"session_id": session_id, "device_id": device_id, "seq": seq, "text": text, "partial": partial, "source": source, "accepted": accepted, "transcript_id": transcript_id, "queued": bool(transcript_id and text and not partial), "ts_ms": now}
        self._event("relay_transcript", payload)
        return {"ok": True, **payload}

    def complete_transcript(self, transcript_id: int, session_id: str, device_id: str, response_text: str | None = None, error: str | None = None, now_ms: int | None = None) -> None:
        now = self._now(now_ms)
        self.store.update_transcript_result(transcript_id, processed_at_ms=None if error else now, response_text=response_text, error=error)
        event_type = "relay_llm_error" if error else "relay_llm_output"
        self._event(event_type, {"session_id": session_id, "device_id": device_id, "transcript_id": transcript_id, "text": response_text, "error": error, "ts_ms": now})

    def pending_transcripts(self, limit: int = 100) -> dict[str, Any]:
        return {"ok": True, "items": self.store.list_pending_transcripts(limit=limit)}

    def failed_transcripts(self, limit: int = 100) -> dict[str, Any]:
        return {"ok": True, "items": self.store.list_failed_transcripts(limit=limit)}

    def retry_transcript(self, transcript_id: int, now_ms: int | None = None) -> dict[str, Any]:
        now = self._now(now_ms)
        item = self.store.retry_transcript(transcript_id, queued_at_ms=now)
        if not item:
            raise RelayError(f"Unknown transcript: {transcript_id}", 404)
        self._event("relay_transcript_retry_queued", {"session_id": item["session_id"], "device_id": item["device_id"], "transcript_id": transcript_id, "ts_ms": now})
        return {"ok": True, "item": item}

    def list_events(self, after_id: int = 0, session_id: str | None = None, limit: int = 500) -> dict[str, Any]:
        return {"ok": True, "events": self.store.list_events(after_id=after_id, session_id=session_id, limit=limit)}

    def timeline(self, session_id: str, after_id: int = 0) -> dict[str, Any]:
        session = self.store.get_session(session_id)
        return {"ok": True, "session": self._public_session(session) if session else None, "events": self.store.list_events(after_id=after_id, session_id=session_id), "transcripts": self.store.list_transcripts(session_id)}

    def _grant(self, session_id: str, device_id: str, owner_id: str, now: int, *, state: str, event_type: str) -> dict[str, Any]:
        token = self._lease_token()
        resume_token = self._resume_token()
        lease = self.store.save_lease({"session_id": session_id, "device_id": device_id, "lease_token": None, "lease_token_hash": self._hash_secret(token), "lease_expires_at_ms": now + self.lease_ttl_ms, "updated_at_ms": now, "revoked_at_ms": None, "revoked_reason": None})
        previous = self.store.get_session(session_id) or {}
        expected_seq = int(previous.get("expected_seq") or ((previous.get("last_seq") + 1) if previous.get("last_seq") is not None else 0))
        session = self.store.upsert_session({**previous, "session_id": session_id, "owner_id": owner_id, "active_device_id": device_id, "state": state, "resume_token_hash": self._hash_secret(resume_token), "expected_seq": expected_seq, "updated_at_ms": now})
        self._event(event_type, {"session_id": session_id, "device_id": device_id, "lease_version": lease.get("lease_version"), "ts_ms": now})
        return {"ok": True, **self._public_session(session), **self._public_lease(lease), "lease_token": token, "resume_token": resume_token, "transport": {"type": "websocket", "endpoint": f"/relay/sessions/{session_id}/stream"}}

    def _require_device(self, device_id: str) -> dict[str, Any]:
        device = self.store.get_device(device_id)
        if not device:
            raise RelayError(f"Unknown device: {device_id}", 404)
        return device

    def _require_device_auth(self, device_id: str, device_token: str | None) -> dict[str, Any]:
        device = self._require_device(device_id)
        if device.get("status") == "revoked":
            raise RelayError("Relay device is revoked", 403)
        token_hash = device.get("token_hash")
        if not token_hash:
            raise RelayError("Relay device token enrollment required", 401)
        if device_token and self._secret_matches(device_token, token_hash):
            return device
        raise RelayError("Invalid relay device token", 401)

    def _require_trusted_force_target(self, device_id: str) -> dict[str, Any]:
        device = self._require_device(device_id)
        if device.get("status") == "revoked":
            raise RelayError("Relay device is revoked", 403)
        if not device.get("token_hash"):
            raise RelayError("Relay device token enrollment required", 401)
        if not device.get("trusted"):
            raise RelayError("Force handoff target must be trusted or prove device token", 403)
        return device

    def _require_active_lease(self, session_id: str, device_id: str, lease_token: str, now: int) -> None:
        lease = self.store.get_lease(session_id)
        if not lease or lease["device_id"] != device_id or not self._lease_matches(lease, lease_token):
            raise RelayError("Invalid or stale lease", 409)
        if lease.get("revoked_at_ms") is not None:
            raise RelayError("Lease revoked", 409)
        if lease["lease_expires_at_ms"] <= now:
            raise RelayError("Lease expired", 409)

    def _event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = self.store.log_event(event_type, payload)
        if self.event_hook:
            self.event_hook(event_type, {**payload, "relay_event_id": event.get("id")})
        return event

    def _lease_matches(self, lease: dict[str, Any], token: str) -> bool:
        if lease.get("lease_token_hash"):
            return self._secret_matches(token, lease.get("lease_token_hash"))
        return hmac.compare_digest(lease.get("lease_token") or "", token)

    @staticmethod
    def _close_missing_seq(missing_ranges: list[list[int]], seq: int) -> list[list[int]]:
        closed: list[list[int]] = []
        for start, end in missing_ranges:
            if seq < start or seq > end:
                closed.append([start, end])
                continue
            if start <= seq - 1:
                closed.append([start, seq - 1])
            if seq + 1 <= end:
                closed.append([seq + 1, end])
        return closed

    @staticmethod
    def _public_device(device: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in device.items() if k != "token_hash"}

    @staticmethod
    def _public_session(session: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in session.items() if k not in {"resume_token_hash"}}

    @staticmethod
    def _public_lease(lease: dict[str, Any] | None) -> dict[str, Any] | None:
        if lease is None:
            return None
        return {k: v for k, v in lease.items() if k not in {"lease_token", "lease_token_hash"}}

    @staticmethod
    def _hash_secret(value: str | None) -> str | None:
        if not value:
            return None
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _secret_matches(cls, value: str | None, expected_hash: str | None) -> bool:
        return bool(value and expected_hash and hmac.compare_digest(cls._hash_secret(value) or "", expected_hash))

    @staticmethod
    def _lease_token() -> str:
        return "lt_" + secrets.token_urlsafe(24)

    @staticmethod
    def _resume_token() -> str:
        return "rt_" + secrets.token_urlsafe(24)

    @staticmethod
    def _device_token() -> str:
        return "dt_" + secrets.token_urlsafe(24)

    @staticmethod
    def _now(value: int | None = None) -> int:
        return int(value if value is not None else current_time_ms())
