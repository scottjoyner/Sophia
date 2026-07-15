from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class WsMessage(BaseModel):
    type: str
    payload: dict[str, Any]
