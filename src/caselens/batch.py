"""Bounded worker queue with content/config-aware SQLite checkpoints."""

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

from caselens.errors import ClientError


class BatchRunner:
    def __init__(self, path: Path, fingerprint: str, concurrency=4):
        if not 1 <= concurrency <= 32:
            raise ValueError("concurrency must be between 1 and 32")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS jobs (key TEXT PRIMARY KEY, path TEXT NOT NULL, status TEXT NOT NULL, result TEXT)")
        self.fingerprint, self.concurrency = fingerprint, concurrency

    def key(self, path: Path):
        digest = hashlib.sha256()
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("image is larger than 20 MiB")
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return hashlib.sha256((self.fingerprint + str(path.resolve()) + digest.hexdigest()).encode()).hexdigest()

    async def run(self, paths, process, on_result, *, resume=False):
        queue = asyncio.Queue(maxsize=self.concurrency * 2)
        counts = {"completed": 0, "needs_review": 0, "partial": 0, "failed": 0, "resumed": 0}

        async def worker():
            while True:
                path = await queue.get()
                try:
                    if path is None:
                        return
                    key = None
                    row = {"path": str(path.resolve()), "status": "failed", "resumed": False}
                    try:
                        key = self.key(path)
                        saved = self.connection.execute("SELECT status, result FROM jobs WHERE key=?", (key,)).fetchone()
                        if resume and saved and saved[0] == "completed":
                            row = json.loads(saved[1])
                            row["resumed"] = True
                        else:
                            with self.connection:
                                self.connection.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?, 'running', NULL)", (key, str(path.resolve())))
                            result = await process(path)
                            row.update(status=result.status, result=result.model_dump(mode="json"))
                    except (ValueError, OSError):
                        row["error"] = {"code": "invalid_input", "message": "input is invalid or unavailable"}
                    except ClientError as exc:
                        row["error"] = {"code": exc.code, "message": exc.message}
                    except Exception:
                        row["error"] = {"code": "execution_failed", "message": "unexpected task failure; inspect application tests and configuration"}
                    if key and not row["resumed"]:
                        with self.connection:
                            self.connection.execute("UPDATE jobs SET status=?, result=? WHERE key=?", (row["status"], json.dumps(row, ensure_ascii=False), key))
                    counts[row["status"]] += 1
                    counts["resumed"] += int(row["resumed"])
                    on_result(row)
                finally:
                    queue.task_done()

        # TaskGroup cancels peers if the producer or output callback fails; no hidden orphan workers.
        async with asyncio.TaskGroup() as group:
            for _ in range(self.concurrency):
                group.create_task(worker())
            for path in paths:
                await queue.put(path)
            for _ in range(self.concurrency):
                await queue.put(None)
        return counts

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
