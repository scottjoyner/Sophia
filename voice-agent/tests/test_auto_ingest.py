from __future__ import annotations

import neo4j

from voice_agent.auth import neo4j_ingest


class _FakeNeo4j:
    """In-memory stand-in for the Neo4j audio-file staging graph."""

    def __init__(self) -> None:
        self.files: dict[str, dict] = {}
        # Path of the most recent collect query, for assertions if needed.
        self.last_query: str | None = None

    @classmethod
    def driver(cls, uri, auth):
        return cls._instance

    def seed(self, container_path: str, *, status: str = "pending") -> None:
        self.files[container_path] = {
            "storage_tier": "ssd_staging",
            "ingest_status": status,
            "container_path": container_path,
        }

    def collect(self, *, speaker_node_id=None, speaker_name=None, database=None, limit=200):
        return neo4j_ingest.collect_audio_paths_from_neo4j(
            "bolt://x", "neo4j", "pw",
            speaker_node_id=speaker_node_id,
            speaker_name=speaker_name,
            database=database,
            limit=limit,
        )

    def run(self, query, **params):
        self.last_query = query
        if "ingest_status = 'enrolled'" in query:
            advanced = 0
            for f in self.files.values():
                if (
                    f["storage_tier"] == "ssd_staging"
                    and f["ingest_status"] == "pending"
                    and f["container_path"] in params.get("paths", [])
                ):
                    f["ingest_status"] = "enrolled"
                    f["enrolled_user_id"] = params.get("user_id")
                    f["enrolled_version_id"] = params.get("version_id")
                    advanced += 1
            res = type("Res", (), {"single": lambda self: {"advanced": advanced}})()
            return res
        if "count(file)" in query or "RETURN count(" in query:
            paths = set(params.get("paths", []))
            adv = sum(
                1 for f in self.files.values()
                if f["storage_tier"] == "ssd_staging"
                and f["ingest_status"] == "pending"
                and f["container_path"] in paths
            )
            return type("Res", (), {"single": lambda self: {"advanced": adv}})()
        out = []
        for path, f in self.files.items():
            if f["storage_tier"] == "ssd_staging" and f["ingest_status"] == "pending":
                out.append(path)
        return [type("R", (), {"get": lambda self, k, d=None, _p=path: _p})() for path in out]

    def session(self, database=None):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


def test_collect_scopes_to_pending_ssd_staging(monkeypatch) -> None:
    fake = _FakeNeo4j()
    _FakeNeo4j._instance = fake
    fake.seed("/ssd-ingest/a.wav", status="pending")
    fake.seed("/ssd-ingest/b.wav", status="enrolled")
    fake.seed("/ssd-ingest/c.wav", status="pending")
    fake.files["/elsewhere/other.wav"] = {
        "storage_tier": "mobile",
        "ingest_status": "pending",
        "container_path": "/elsewhere/other.wav",
    }
    monkeypatch.setattr(neo4j, "GraphDatabase", fake)

    paths = fake.collect()
    assert set(paths) == {"/ssd-ingest/a.wav", "/ssd-ingest/c.wav"}


def test_collect_speaker_queries_return_paths(monkeypatch) -> None:
    fake = _FakeNeo4j()
    _FakeNeo4j._instance = fake

    def run(self, query, **params):
        rec = type("R", (), {"get": lambda self, k, d=None, _p="/spk/x.wav": _p})()
        return [rec]

    fake.run = run.__get__(fake)
    monkeypatch.setattr(neo4j, "GraphDatabase", fake)

    assert fake.collect(speaker_name="scott") == ["/spk/x.wav"]
    assert fake.collect(speaker_node_id=42) == ["/spk/x.wav"]


def test_mark_audio_files_enrolled_advances_pending(monkeypatch) -> None:
    fake = _FakeNeo4j()
    _FakeNeo4j._instance = fake
    fake.seed("/ssd-ingest/a.wav", status="pending")
    fake.seed("/ssd-ingest/b.wav", status="pending")
    monkeypatch.setattr(neo4j, "GraphDatabase", fake)

    advanced = neo4j_ingest.mark_audio_files_enrolled(
        "bolt://x", "neo4j", "pw",
        paths=["/ssd-ingest/a.wav", "/ssd-ingest/missing.wav"],
        enrolled_user_id="scott",
        version_id="v1",
    )
    assert advanced == 1
    assert fake.files["/ssd-ingest/a.wav"]["ingest_status"] == "enrolled"
    assert fake.files["/ssd-ingest/a.wav"]["enrolled_user_id"] == "scott"
    assert fake.files["/ssd-ingest/a.wav"]["enrolled_version_id"] == "v1"
    assert fake.files["/ssd-ingest/b.wav"]["ingest_status"] == "pending"
