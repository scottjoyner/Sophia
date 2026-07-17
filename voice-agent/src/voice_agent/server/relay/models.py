from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DeviceTrustRequest(BaseModel):
    trusted: bool = True


class DeviceRevokeRequest(BaseModel):
    reason: str = "revoked"


class WebRTCOfferRequest(BaseModel):
    device_id: str = Field(min_length=1)
    device_token: str | None = None
    lease_token: str | None = None
    sdp: str = ""
    type: str = "offer"


class DeviceRegisterRequest(BaseModel):
    device_id: str = Field(min_length=1)
    device_token: str | None = None
    name: str = ""
    owner_id: str = "scott"
    capabilities: list[str] = Field(default_factory=list)
    platform: str = ""
    mesh_node: str = ""
    enrollment_token: str | None = None
    fallback_priority: int = 100
    trusted: bool = False


class DeviceHeartbeatRequest(BaseModel):
    device_id: str = Field(min_length=1)
    device_token: str | None = None
    session_id: str | None = None
    lease_token: str | None = None
    seq: int | None = None
    ts_ms: int | None = None


class AttachRequest(BaseModel):
    device_id: str = Field(min_length=1)
    owner_id: str = "scott"
    device_token: str | None = None
    resume_token: str | None = None
    preferred_transport: str = "websocket"
    force: bool = False


class HandoffRequest(BaseModel):
    from_device_id: str | None = None
    to_device_id: str = Field(min_length=1)
    reason: str = "manual"
    lease_token: str | None = None
    device_token: str | None = None
    force: bool = False


class DetachRequest(BaseModel):
    device_id: str = Field(min_length=1)
    lease_token: str | None = None
    device_token: str | None = None


class AudioChunkRequest(BaseModel):
    device_id: str = Field(min_length=1)
    device_token: str | None = None
    lease_token: str = Field(min_length=1)
    seq: int = Field(ge=0)
    encoding: str = "pcm_s16le"
    payload_b64: str | None = None
    byte_count: int | None = None


class TranscriptRequest(BaseModel):
    device_id: str = Field(min_length=1)
    device_token: str | None = None
    lease_token: str = Field(min_length=1)
    seq: int = Field(ge=0)
    text: str = ""
    partial: bool = False
    source: str = "stt"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResumeRequest(BaseModel):
    device_id: str = Field(min_length=1)
    device_token: str | None = None
    resume_token: str = Field(min_length=1)
    last_seen_event_id: int = 0
    last_seen_seq: int | None = None


class ForceHandoffRequest(HandoffRequest):
    force: bool = True
