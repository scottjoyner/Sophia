from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voice_agent.config import load_config
from voice_agent.server.app import create_app
from voice_agent.server.graph_outbox import GraphOutbox
from voice_agent.util.audio import write_wav


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SOPHIA_APP_PASSWORD", "test-pass")
    monkeypatch.setenv("SOPHIA_SESSION_SECRET", "test-secret")
    monkeypatch.setenv("NEO4J_PASSWORD", "fakepw")  # force a write attempt that will fail -> enqueue
    cfg = load_config(None)
    cfg.paths.artifacts_dir = str(tmp_path / "runs")
    cfg.paths.workspace_dir = str(tmp_path / "workspace")
    return TestClient(create_app(cfg))


def test_pwa_assets_served(client: TestClient) -> None:
    for path, ctype in [
        ("/static/manifest.webmanifest", "application/manifest+json"),
        ("/sw.js", "application/javascript"),
        ("/static/icons/icon.svg", "image/svg+xml"),
    ]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert ctype in (r.headers.get("content-type") or ""), path


def test_manifest_links_pwa_meta(client: TestClient) -> None:
    html = client.get("/").text
    assert 'rel="manifest"' in html
    assert "apple-mobile-web-app-capable" in html
    assert "navigator.serviceWorker.register" in html


def test_capture_enqueues_graph_outbox_on_failure(client: TestClient, tmp_path: Path) -> None:
    wav = tmp_path / "v.wav"
    write_wav(wav, (0.3 * np.sin(2 * np.pi * 220 * np.arange(16000 * 3) / 16000)).astype(np.float32), 16000)
    r = client.post(
        "/capture",
        files={"audio": ("v.wav", wav.read_bytes(), "audio/wav")},
        data={"user_id": "scott", "client_capture_id": "cli-abc"},
    )
    assert r.status_code == 200
    assert r.json().get("graph_error")
    # The failed write must land in the graph outbox for the supervisor to replay.
    outbox = client.app.state.graph_outbox
    items = outbox.list_due(limit=10)
    keys = [i.idempotency_key for i in items]
    assert "server:cli-abc" in keys
