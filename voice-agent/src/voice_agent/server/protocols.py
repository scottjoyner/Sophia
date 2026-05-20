from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class NormalizedMessage:
    msg_type: str
    payload: Dict[str, Any]


class ProtocolAdapter:
    def decode(self, data: Dict[str, Any]) -> NormalizedMessage:
        raise NotImplementedError


class NativeWsAdapter(ProtocolAdapter):
    def decode(self, data: Dict[str, Any]) -> NormalizedMessage:
        return NormalizedMessage(msg_type=data.get("type", ""), payload=data.get("payload", {}))


class HermesOverlayAdapter(ProtocolAdapter):
    """Accepts hermes-agent style envelope with transport metadata.

    Expected input shape:
    {
      "protocol": "hermes_overlay_v1",
      "frame": {"type": "audio_chunk", "payload": {...}},
      "meta": {...}
    }
    """

    def decode(self, data: Dict[str, Any]) -> NormalizedMessage:
        frame = data.get("frame", {})
        return NormalizedMessage(msg_type=frame.get("type", ""), payload=frame.get("payload", {}))


def build_protocol_adapter(name: str) -> ProtocolAdapter:
    if name == "hermes_overlay_v1":
        return HermesOverlayAdapter()
    return NativeWsAdapter()
