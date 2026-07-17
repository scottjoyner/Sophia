from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from ..events import EventBus, event_to_dict
from .broker import RelayBroker, RelayError
from .models import (
    AttachRequest,
    AudioChunkRequest,
    DetachRequest,
    DeviceHeartbeatRequest,
    DeviceRegisterRequest,
    DeviceRevokeRequest,
    DeviceTrustRequest,
    ForceHandoffRequest,
    HandoffRequest,
    ResumeRequest,
    TranscriptRequest,
    WebRTCAnswerRequest,
    WebRTCIceCandidateRequest,
    WebRTCOfferRequest,
)
from .worker import RelayTurnWorker


def create_relay_router(
    broker: RelayBroker,
    bus: EventBus | None = None,
    worker: RelayTurnWorker | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/relay", tags=["relay"])

    def handle_error(exc: RelayError) -> None:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    def require_admin(x_relay_admin_token: str | None) -> None:
        expected = os.getenv("TOMMY_RELAY_ADMIN_TOKEN") or os.getenv("SOPHIA_RELAY_ADMIN_TOKEN")
        if not expected or not x_relay_admin_token or not hmac.compare_digest(x_relay_admin_token, expected):
            raise HTTPException(status_code=401, detail="Invalid relay admin token")

    def enqueue_turn(result: dict[str, Any]) -> None:
        if not worker or not result.get("queued") or not result.get("transcript_id"):
            return
        queued = worker.enqueue(
            {
                "transcript_id": result["transcript_id"],
                "session_id": result["session_id"],
                "device_id": result["device_id"],
                "seq": result["seq"],
                "text": result["text"],
            }
        )
        result["assistant_turn_queued"] = queued
        if not queued:
            broker.complete_transcript(
                result["transcript_id"],
                result["session_id"],
                result["device_id"],
                error="relay turn queue is full",
            )

    @router.post("/devices/register")
    async def register_device(req: DeviceRegisterRequest) -> dict[str, Any]:
        try:
            return broker.register_device(
                req.device_id,
                req.name,
                req.owner_id,
                req.capabilities,
                req.platform,
                req.mesh_node,
                enrollment_token=req.enrollment_token,
                device_token=req.device_token,
                fallback_priority=req.fallback_priority,
                trusted=req.trusted,
                audio_source=req.audio_source,
                tailscale_ip=req.tailscale_ip,
                location=req.location,
            )
        except RelayError as exc:
            handle_error(exc)

    @router.post("/devices/heartbeat")
    async def heartbeat(req: DeviceHeartbeatRequest) -> dict[str, Any]:
        try:
            return broker.heartbeat(req.device_id, req.session_id, req.lease_token, req.seq, req.ts_ms, device_token=req.device_token)
        except RelayError as exc:
            handle_error(exc)

    @router.get("/devices")
    async def devices(x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        return broker.list_devices()

    @router.post("/devices/{device_id}/trust")
    async def trust_device(device_id: str, req: DeviceTrustRequest, x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        try:
            return broker.set_device_trust(device_id, trusted=req.trusted)
        except RelayError as exc:
            handle_error(exc)

    @router.post("/devices/{device_id}/rotate-token")
    async def rotate_device_token(device_id: str, x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        try:
            return broker.rotate_device_token(device_id)
        except RelayError as exc:
            handle_error(exc)

    @router.post("/devices/{device_id}/revoke")
    async def revoke_device(device_id: str, req: DeviceRevokeRequest, x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        try:
            return broker.revoke_device(device_id, reason=req.reason)
        except RelayError as exc:
            handle_error(exc)

    @router.get("/dashboard", response_class=HTMLResponse)
    async def relay_dashboard(x_relay_admin_token: str | None = Header(default=None)) -> str:
        require_admin(x_relay_admin_token)
        return """<!doctype html><html><head><title>Relay Dashboard</title>
<style>body{font-family:system-ui;background:#0b1020;color:#e7ecff;margin:2rem}pre{background:#151b33;padding:1rem;border-radius:8px;overflow:auto}.grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}button{padding:.5rem 1rem}</style></head>
<body><h1>Relay Dashboard</h1><p>Monitor Tommy telescope mesh devices, active leases, heartbeats, missing audio ranges, pending assistant outbox work, events, and force-handoff controls.</p>
<button id=force-handoff>force-handoff</button><button onclick=refresh()>refresh</button>
<div class=grid><section><h2>Devices</h2><pre id=devices></pre></section><section><h2>Sessions / active device / lease TTL</h2><pre id=sessions></pre></section><section><h2>Tommy gaps</h2><pre id=gaps></pre></section><section><h2>Pending outbox</h2><pre id=outbox></pre></section></div><section><h2>Events</h2><pre id=events></pre></section>
<script>
async function refresh(){
  const token = sessionStorage.tommyRelayAdminToken || prompt('Relay admin token');
  if (token) sessionStorage.tommyRelayAdminToken = token;
  const headers = token ? {'x-relay-admin-token': token} : {};
  const get = p => fetch(p, {headers}).then(r=>r.json());
  const [devices, sessions, gaps, outbox, events] = await Promise.all([get('/relay/devices'), get('/relay/sessions'), get('/relay/sessions/tommy/gaps'), get('/relay/outbox/pending?limit=20'), get('/relay/events?session_id=tommy&limit=100')]);
  document.querySelector('#devices').textContent = JSON.stringify(devices, null, 2);
  document.querySelector('#sessions').textContent = JSON.stringify(sessions, null, 2);
  document.querySelector('#gaps').textContent = JSON.stringify(gaps, null, 2);
  document.querySelector('#outbox').textContent = JSON.stringify(outbox, null, 2);
  document.querySelector('#events').textContent = JSON.stringify(events, null, 2);
}
refresh(); setInterval(refresh, 5000);
</script></body></html>"""

    @router.get("/sessions")
    async def sessions(x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        return broker.list_sessions()

    @router.post("/sessions/{session_id}/attach")
    async def attach(session_id: str, req: AttachRequest) -> dict[str, Any]:
        if req.force:
            raise HTTPException(status_code=401, detail="force attach requires /force-handoff with relay admin token")
        try:
            return broker.attach(
                session_id,
                req.device_id,
                req.owner_id,
                device_token=req.device_token,
                resume_token=req.resume_token,
                force=req.force,
            )
        except RelayError as exc:
            handle_error(exc)

    @router.post("/sessions/{session_id}/resume")
    async def resume(session_id: str, req: ResumeRequest) -> dict[str, Any]:
        try:
            return broker.resume(
                session_id,
                req.device_id,
                req.resume_token,
                device_token=req.device_token,
                last_seen_event_id=req.last_seen_event_id,
                last_seen_seq=req.last_seen_seq,
            )
        except RelayError as exc:
            handle_error(exc)

    @router.post("/sessions/{session_id}/handoff")
    async def handoff(session_id: str, req: HandoffRequest) -> dict[str, Any]:
        if req.force:
            raise HTTPException(status_code=401, detail="force handoff requires /force-handoff with relay admin token")
        try:
            return broker.handoff(
                session_id,
                req.from_device_id,
                req.to_device_id,
                req.reason,
                lease_token=req.lease_token,
                device_token=req.device_token,
                force=req.force,
            )
        except RelayError as exc:
            handle_error(exc)

    @router.post("/sessions/{session_id}/force-handoff")
    async def force_handoff(session_id: str, req: ForceHandoffRequest, x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        try:
            return broker.handoff(
                session_id,
                req.from_device_id,
                req.to_device_id,
                req.reason or "force_handoff",
                lease_token=req.lease_token,
                device_token=req.device_token,
                force=True,
            )
        except RelayError as exc:
            handle_error(exc)

    @router.post("/sessions/{session_id}/detach")
    async def detach(session_id: str, req: DetachRequest) -> dict[str, Any]:
        try:
            return broker.detach(session_id, req.device_id, req.lease_token, device_token=req.device_token)
        except RelayError as exc:
            handle_error(exc)

    @router.post("/sessions/{session_id}/expire")
    async def expire(session_id: str, x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        try:
            return broker.expire_session(session_id)
        except RelayError as exc:
            handle_error(exc)

    @router.get("/sessions/{session_id}")
    async def status(session_id: str, x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        try:
            return broker.session_status(session_id)
        except RelayError as exc:
            handle_error(exc)

    @router.get("/sessions/{session_id}/timeline")
    async def timeline(session_id: str, after_id: int = 0, x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        return broker.timeline(session_id, after_id)

    @router.get("/sessions/{session_id}/gaps")
    async def gaps(session_id: str, x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        status_body = broker.session_status(session_id)
        return {"ok": True, "session_id": session_id, "missing_ranges": status_body.get("missing_ranges", []), "expected_seq": status_body.get("expected_seq")}

    @router.post("/sessions/{session_id}/audio")
    async def audio(session_id: str, req: AudioChunkRequest) -> dict[str, Any]:
        try:
            byte_count = req.byte_count
            if byte_count is None and req.payload_b64:
                byte_count = len(base64.b64decode(req.payload_b64))
            return broker.record_audio_chunk(
                session_id,
                req.device_id,
                req.lease_token,
                req.seq,
                req.encoding,
                byte_count or 0,
                device_token=req.device_token,
            )
        except RelayError as exc:
            handle_error(exc)

    @router.post("/sessions/{session_id}/transcript")
    async def transcript(session_id: str, req: TranscriptRequest) -> dict[str, Any]:
        try:
            result = broker.record_transcript(
                session_id,
                req.device_id,
                req.lease_token,
                req.seq,
                req.text,
                req.partial,
                req.source,
                req.metadata,
                device_token=req.device_token,
            )
        except RelayError as exc:
            handle_error(exc)
        enqueue_turn(result)
        return result

    @router.post("/sessions/{session_id}/webrtc/offer")
    async def webrtc_offer(session_id: str, req: WebRTCOfferRequest) -> dict[str, Any]:
        try:
            return broker.webrtc_offer(session_id, req.device_id, req.sdp, req.type, lease_token=req.lease_token, device_token=req.device_token)
        except RelayError as exc:
            handle_error(exc)

    @router.get("/sessions/{session_id}/webrtc/offers/pending")
    async def pending_webrtc_offers(session_id: str, limit: int = 100, x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        return broker.pending_webrtc_offers(session_id, limit=max(1, min(limit, 500)))

    @router.post("/sessions/{session_id}/webrtc/offers/{offer_id}/answer")
    async def webrtc_answer(session_id: str, offer_id: str, req: WebRTCAnswerRequest) -> dict[str, Any]:
        try:
            return broker.webrtc_answer(session_id, offer_id, req.device_id, req.sdp, req.type, lease_token=req.lease_token, device_token=req.device_token)
        except RelayError as exc:
            handle_error(exc)

    @router.post("/sessions/{session_id}/webrtc/offers/{offer_id}/candidate")
    async def webrtc_candidate(session_id: str, offer_id: str, req: WebRTCIceCandidateRequest) -> dict[str, Any]:
        try:
            return broker.webrtc_candidate(session_id, offer_id, req.device_id, req.candidate, req.sdp_mid, req.sdp_mline_index, lease_token=req.lease_token, device_token=req.device_token)
        except RelayError as exc:
            handle_error(exc)

    @router.get("/events")
    async def relay_events(after_id: int = 0, session_id: str | None = None, limit: int = 500, x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        return broker.list_events(after_id=after_id, session_id=session_id, limit=limit)

    @router.get("/outbox/pending")
    async def pending_outbox(limit: int = 100, x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        return broker.pending_transcripts(limit=max(1, min(limit, 500)))

    @router.get("/outbox/failed")
    async def failed_outbox(limit: int = 100, x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        return broker.failed_transcripts(limit=max(1, min(limit, 500)))

    @router.post("/outbox/{transcript_id}/retry")
    async def retry_outbox(transcript_id: int, x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        try:
            result = broker.retry_transcript(transcript_id)
        except RelayError as exc:
            handle_error(exc)
            raise
        if worker:
            worker.enqueue(
                {
                    "transcript_id": result["item"]["id"],
                    "session_id": result["item"]["session_id"],
                    "device_id": result["item"]["device_id"],
                    "seq": result["item"]["seq"],
                    "text": result["item"]["text"],
                }
            )
        return result

    @router.get("/health")
    async def health(x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        pending = broker.pending_transcripts(limit=1)
        return {"ok": True, "worker": worker.status() if worker else None, "pending_outbox_count_nonzero": bool(pending["items"])}

    @router.get("/readiness")
    async def readiness(x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_relay_admin_token)
        return {"ok": True, "devices": len(broker.list_devices()["devices"]), "worker": worker.status() if worker else None}

    @router.websocket("/sessions/{session_id}/stream")
    async def relay_stream(websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        event_queue = bus.subscribe() if bus is not None else None
        sender: asyncio.Task[None] | None = None
        authenticated = False
        auth_device_id: str | None = None
        auth_device_token: str | None = None
        auth_lease_token: str | None = None

        async def push_events() -> None:
            if event_queue is None:
                return
            while True:
                event = await event_queue.get()
                if not event.type.startswith("relay_"):
                    continue
                if event.payload.get("session_id") != session_id:
                    continue
                try:
                    ensure_authenticated()
                except RelayError as exc:
                    await websocket.send_text(json.dumps({"type": "error", "error": str(exc), "status_code": exc.status_code}))
                    await websocket.close(code=1008)
                    return
                await websocket.send_text(json.dumps({"type": "event", "payload": event_to_dict(event)}))

        def ensure_authenticated() -> None:
            if not authenticated or not auth_device_id or not auth_lease_token:
                raise RelayError("Relay websocket authentication required", 401)
            result = broker.validate_active_lease(session_id, auth_device_id, auth_lease_token, device_token=auth_device_token)
            if not result.get("active"):
                raise RelayError("Relay websocket active lease required", 401)

        try:
            while True:
                data = json.loads(await websocket.receive_text())
                msg_type = data.get("type")
                if msg_type == "authenticate":
                    payload = DeviceHeartbeatRequest(**data)
                    if not payload.lease_token:
                        raise RelayError("Relay websocket authentication requires an active lease token", 401)
                    result = broker.heartbeat(payload.device_id, payload.session_id or session_id, payload.lease_token, payload.seq, payload.ts_ms, device_token=payload.device_token)
                    if not result.get("active"):
                        raise RelayError("Relay websocket authentication requires an active session lease", 401)
                    authenticated = True
                    auth_device_id = payload.device_id
                    auth_device_token = payload.device_token
                    auth_lease_token = payload.lease_token
                    if event_queue is not None and sender is None:
                        sender = asyncio.create_task(push_events())
                    await websocket.send_text(json.dumps({"type": "authenticated", "payload": result}))
                elif msg_type == "audio_chunk":
                    ensure_authenticated()
                    payload = AudioChunkRequest(**data)
                    byte_count = payload.byte_count
                    if byte_count is None and payload.payload_b64:
                        byte_count = len(base64.b64decode(payload.payload_b64))
                    result = broker.record_audio_chunk(
                        session_id,
                        payload.device_id,
                        payload.lease_token,
                        payload.seq,
                        payload.encoding,
                        byte_count or 0,
                        device_token=payload.device_token or auth_device_token,
                    )
                    await websocket.send_text(json.dumps({"type": "ack", "payload": result}))
                elif msg_type == "transcript":
                    ensure_authenticated()
                    payload = TranscriptRequest(**data)
                    result = broker.record_transcript(
                        session_id,
                        payload.device_id,
                        payload.lease_token,
                        payload.seq,
                        payload.text,
                        payload.partial,
                        payload.source,
                        payload.metadata,
                        device_token=payload.device_token or auth_device_token,
                    )
                    enqueue_turn(result)
                    await websocket.send_text(json.dumps({"type": "transcript", "payload": result}))
                elif msg_type == "resume":
                    ensure_authenticated()
                    payload = ResumeRequest(**data)
                    result = broker.resume(session_id, payload.device_id, payload.resume_token, device_token=payload.device_token, last_seen_event_id=payload.last_seen_event_id, last_seen_seq=payload.last_seen_seq)
                    await websocket.send_text(json.dumps({"type": "resume", "payload": result}))
                elif msg_type == "heartbeat":
                    ensure_authenticated()
                    payload = DeviceHeartbeatRequest(**data)
                    result = broker.heartbeat(payload.device_id, payload.session_id or session_id, payload.lease_token, payload.seq, payload.ts_ms, device_token=payload.device_token)
                    await websocket.send_text(json.dumps({"type": "heartbeat", "payload": result}))
                elif msg_type == "events":
                    ensure_authenticated()
                    events = broker.list_events(session_id=session_id)["events"]
                    await websocket.send_text(json.dumps({"type": "events", "payload": events}))
                else:
                    await websocket.send_text(json.dumps({"type": "error", "error": f"unsupported relay frame: {msg_type}"}))
        except WebSocketDisconnect:
            return
        except (RelayError, ValidationError) as exc:
            status_code = exc.status_code if isinstance(exc, RelayError) else 422
            await websocket.send_text(json.dumps({"type": "error", "error": str(exc), "status_code": status_code}))
        finally:
            if sender:
                sender.cancel()
            if bus is not None and event_queue is not None:
                bus.unsubscribe(event_queue)

    if bus is not None:

        @router.get("/live-events")
        async def live_events(after_id: int = 0, session_id: str | None = None, x_relay_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
            require_admin(x_relay_admin_token)
            records = bus.snapshot(after_id=after_id, session_id=session_id)
            return {"events": [event_to_dict(event) for event in records if event.type.startswith("relay_")]}

    return router
