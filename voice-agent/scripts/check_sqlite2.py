#!/usr/bin/env python3
"""Check SQLite after enrollment."""
import json
import sqlite3

conn = sqlite3.connect('/data/runs/results.sqlite')
conn.row_factory = sqlite3.Row

cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print(f'Tables: {tables}')

if 'voiceprints' in tables:
    cur = conn.execute('SELECT user_id, embedding_mean, samples_json, threshold FROM voiceprints')
    for r in cur.fetchall():
        samples = json.loads(r['samples_json']) if r['samples_json'] else {}
        sample_count = len(samples.get('samples', []))
        emb_len = len(json.loads(r['embedding_mean'])) if r['embedding_mean'] else 0
        print(f'  user={r["user_id"]} emb_len={emb_len} samples={sample_count} threshold={r["threshold"]}')
        if sample_count > 0:
            s = samples['samples'][0]
            print(f'    first sample: sha256={s.get("sha256","")[:16]} path={s.get("path","")[:40]}')

cur.close()
