from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel


class WsMessage(BaseModel):
    type: str
    payload: Dict[str, Any]
