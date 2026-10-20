"""SQLite owns reusable observations, never run-specific source references."""

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from caselens.schemas import VisualObservation


class EvidenceStore:
    def __init__(self, path: Path, ttl_seconds: float = 86400):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS observations (key TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL)")
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def key(content_hash: str, model: str, prompt_version: str, question: str) -> str:
        payload = json.dumps([content_hash, model, prompt_version, question], ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(self, key: str) -> VisualObservation | None:
        row = self.connection.execute("SELECT payload, created FROM observations WHERE key=?", (key,)).fetchone()
        if row is None or time.time() - row[1] > self.ttl_seconds:
            return None
        try:
            return VisualObservation.model_validate_json(row[0])
        except ValueError:
            return None

    def put(self, key: str, observation: VisualObservation) -> None:
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO observations VALUES (?, ?, ?)", (key, observation.model_dump_json(), time.time()))

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
