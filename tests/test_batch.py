import asyncio

import pytest

from caselens.batch import BatchRunner
from caselens.schemas import RunResult


@pytest.mark.asyncio
async def test_batch_resume_skips_only_matching_completed_inputs(tmp_path):
    files = [tmp_path / "a.png", tmp_path / "b.png"]
    for file in files:
        file.write_bytes(b"old")
    counts = []
    async def process(path):
        counts.append(path.name)
        return RunResult(mode="replay", status="completed", goal="inspect")
    rows = []
    with BatchRunner(tmp_path / "batch.sqlite3", "config-v1", concurrency=2) as batch:
        await batch.run(files, process, rows.append)
        await batch.run(files, process, rows.append, resume=True)
        files[0].write_bytes(b"changed")
        await batch.run(files, process, rows.append, resume=True)
    assert counts.count("a.png") == 2
    assert counts.count("b.png") == 1
    assert sum(row["resumed"] for row in rows) == 3


@pytest.mark.asyncio
async def test_batch_failure_is_persisted_and_concurrency_is_bounded(tmp_path):
    files = [tmp_path / f"{i}.png" for i in range(8)]
    for file in files:
        file.write_bytes(b"image")
    active = 0
    peak = 0
    async def process(path):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(.01)
        active -= 1
        if path.name == "0.png":
            raise ValueError("bad input")
        return RunResult(mode="replay", status="completed", goal="inspect")
    rows = []
    with BatchRunner(tmp_path / "batch.sqlite3", "v1", concurrency=2) as batch:
        summary = await batch.run(files, process, rows.append)
    assert peak == 2
    assert summary["completed"] == 7
    assert summary["failed"] == 1
    assert len(rows) == 8
    assert next(row for row in rows if row["path"].endswith("0.png"))["error"]["code"] == "invalid_input"


@pytest.mark.asyncio
async def test_resume_does_not_reuse_result_after_config_change(tmp_path):
    file = tmp_path / "a.png"
    file.write_bytes(b"same")
    calls = []
    async def process(path):
        calls.append(path)
        return RunResult(mode="replay", status="completed", goal="inspect")
    for fingerprint in ["policy-v1", "policy-v2"]:
        with BatchRunner(tmp_path / "jobs.sqlite3", fingerprint) as batch:
            await batch.run([file], process, lambda row: None, resume=True)
    assert len(calls) == 2
