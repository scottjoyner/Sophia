from __future__ import annotations

from pathlib import Path

import sqlite3


def generate_report(db_path: str, output_path: str) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM events")
    total = cur.fetchone()[0]
    report = f"# Benchmark Report\n\nTotal events: {total}\n"
    Path(output_path).write_text(report, encoding="utf-8")
