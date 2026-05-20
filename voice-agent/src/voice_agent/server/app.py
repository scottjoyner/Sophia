from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from ..config import AppConfig, load_config
from ..util.audio import b64decode
from .session_manager import SessionManager


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI()
    manager = SessionManager(config, Path(config.paths.artifacts_dir))

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_text()
                data: Dict[str, Any] = json.loads(message)
                msg_type = data.get("type")
                payload = data.get("payload", {})
                if msg_type == "start_session":
                    manager.start_session(
                        payload["session_id"],
                        payload["sample_rate"],
                        payload.get("channels", 1),
                        payload.get("encoding", "pcm_s16le"),
                        payload.get("user_id", "default"),
                    )
                elif msg_type == "audio_chunk":
                    pcm = b64decode(payload["pcm_bytes"])
                    manager.handle_audio_chunk(
                        payload["session_id"],
                        pcm,
                        payload.get("chunk_ms", config.stt.chunk_ms),
                        payload.get("t_client_ms", 0),
                    )
                elif msg_type == "end_session":
                    manager.end_session(payload["session_id"])
                await websocket.send_text(json.dumps({"type": "ack", "payload": {"received": msg_type}}))
        except WebSocketDisconnect:
            return

    return app


def run() -> None:
    import uvicorn

    config = load_config(None)
    app = create_app(config)
    uvicorn.run(app, host=config.server.host, port=config.server.port)
