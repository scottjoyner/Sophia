from pathlib import Path
import tempfile

from voice_agent.server.meeting_task_store import MeetingTaskStore
from voice_agent.util.db import Database


def _store():
    db = Database(Path(tempfile.gettempdir()) / "meeting_task_test.sqlite")
    return MeetingTaskStore(db)


def test_meeting_task_create_update_get():
    s = _store()
    s.create("t1")
    assert s.get("t1")["status"] == "queued"
    s.update("t1", "processing", "diarizing", 20)
    rec = s.get("t1")
    assert rec["status"] == "processing"
    assert rec["progress_pct"] == 20
    assert rec["step"] == "diarizing"


def test_meeting_task_result_roundtrip():
    s = _store()
    s.create("t2")
    s.update("t2", "completed", "done", 100, result={"meeting_id": "m1"})
    rec = s.get("t2")
    assert rec["result"] == {"meeting_id": "m1"}
    assert s.get("missing") is None


def test_meeting_task_survives_restart():
    path = Path(tempfile.gettempdir()) / "meeting_task_persist.sqlite"
    db1 = Database(path)
    s1 = MeetingTaskStore(db1)
    s1.create("p1")
    s1.update("p1", "error", "failed", 0, error="boom")
    # New store on the same file simulates a restart.
    db2 = Database(path)
    s2 = MeetingTaskStore(db2)
    rec = s2.get("p1")
    assert rec["status"] == "error"
    assert rec["error"] == "boom"
