import base64
import io
import json
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from caselens.web import create_app


def wait_job(client, job_id):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(.02)
    pytest.fail("web job did not terminate")


def image_payload():
    stream = io.BytesIO()
    Image.new("RGB", (1, 1), "white").save(stream, format="PNG")
    return {"filename": "photo.png", "content_type": "image/png", "data_base64": base64.b64encode(stream.getvalue()).decode()}


def test_web_create_events_query_and_resume_replay(tmp_path):
    client = TestClient(create_app(state_dir=tmp_path / "state"))
    assert client.get("/api/health").json()["status"] == "ok"
    response = client.post("/api/jobs", json={"text": "包装破损", "order_id": "O-1", "replay": "irrelevant", "images": [image_payload()]})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in {"queued", "running"}:
            break
        time.sleep(.05)
    assert job["status"] == "needs_review"
    assert job["case_id"]
    events = client.get(f"/api/jobs/{job_id}/events").json()["events"]
    assert any(event["type"] == "tool_finished" for event in events)
    case = client.get(f"/api/cases/{job['case_id']}").json()
    assert case["case_id"] == job["case_id"]
    resume = client.post(f"/api/cases/{job['case_id']}/resume", json={"text": "补充订单信息", "order_id": "O-1", "replay": "clear", "images": [image_payload()]})
    assert resume.status_code == 202
    wait_job(client, resume.json()["job_id"])
    assert client.get(f"/api/jobs/{resume.json()['job_id']}").json()["case_id"] == job["case_id"]


def test_web_rejects_malformed_or_oversized_payload(tmp_path):
    client = TestClient(create_app(state_dir=tmp_path / "state"))
    assert client.post("/api/jobs", json={"text": "", "images": []}).status_code == 422
    bad = {"filename": "x.exe", "content_type": "application/octet-stream", "data_base64": "AA=="}
    assert client.post("/api/jobs", json={"text": "test", "images": [bad], "replay": "irrelevant"}).status_code == 422


def test_web_never_returns_configured_credentials(tmp_path):
    client = TestClient(create_app(state_dir=tmp_path / "state"))
    payload = client.get("/api/health").json()
    assert "api_key" not in json.dumps(payload)


def test_web_lists_cases_images_and_reports_after_replay(tmp_path):
    client = TestClient(create_app(state_dir=tmp_path / "state"))
    job = client.post("/api/jobs", json={"text": "包装破损", "order_id": "O-1", "replay": "clear", "images": [image_payload()]}).json()
    wait_job(client, job["job_id"])
    result = client.get(f"/api/jobs/{job['job_id']}").json()["result"]
    cases = client.get("/api/cases").json()["cases"]
    assert cases[0]["case_id"] == result["case_id"]
    asset_id = result["evidence"][0]["asset"]["root_asset_id"]
    assert client.get(f"/api/cases/{result['case_id']}/images/{asset_id}").headers["content-type"] == "image/png"
    assert client.get(f"/api/cases/{result['case_id']}/report").status_code == 200
    stream = client.get(f"/api/jobs/{job['job_id']}/stream")
    assert "text/event-stream" in stream.headers["content-type"]
    assert "job_finished" in stream.text


def test_web_rejects_cross_origin_and_corrupt_images(tmp_path):
    client = TestClient(create_app(state_dir=tmp_path / "state"))
    assert client.post("/api/jobs", headers={"Origin": "https://evil.example"}, json={"text": "test"}).status_code == 403
    bad = {"filename": "x.png", "content_type": "image/png", "data_base64": "AA=="}
    assert client.post("/api/jobs", json={"text": "test", "images": [bad], "replay": "clear"}).status_code == 422


def test_web_live_config_failure_does_not_echo_secret(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"vlm":{"api_key":"private-key","unsupported":"private-key"}}', encoding="utf-8")
    client = TestClient(create_app(state_dir=tmp_path / "state", config_path=path))
    job = client.post("/api/jobs", json={"text": "包装破损"}).json()
    wait_job(client, job["job_id"])
    response = client.get(f"/api/jobs/{job['job_id']}").json()
    assert response["status"] == "failed"
    assert "private-key" not in json.dumps(response)


def test_web_cancelled_task_terminates_and_releases_slot(tmp_path, monkeypatch):
    import asyncio

    import caselens.web as web
    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError
    monkeypatch.setattr(web, "run_customer_case", cancelled)
    client = TestClient(create_app(state_dir=tmp_path / "state"))
    job = client.post("/api/jobs", json={"text": "test", "replay": "clear"}).json()
    assert wait_job(client, job["job_id"])["status"] == "partial"


def test_web_live_jobs_share_runtime_and_close_it(tmp_path, monkeypatch):
    import asyncio

    import caselens.service as service
    import caselens.web as web
    from caselens.schemas import RunResult
    runtimes = []
    used = []
    class FakeRuntime:
        def __init__(self, settings):
            self.settings = settings
            self.closed = False
            runtimes.append(self)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            self.closed = True
    async def run(*args, **kwargs):
        used.append(kwargs["runtime"])
        await asyncio.sleep(.1)
        return RunResult(goal="test", mode="live", status="completed")
    monkeypatch.setattr(service, "Runtime", FakeRuntime)
    monkeypatch.setattr(web, "run_customer_case", run)
    path = tmp_path / "config.json"
    path.write_text('{}', encoding="utf-8")
    with TestClient(create_app(state_dir=tmp_path / "state", config_path=path)) as client:
        first = client.post('/api/jobs', json={"text": "one"}).json()
        second = client.post('/api/jobs', json={"text": "two"}).json()
        assert wait_job(client, first["job_id"])["status"] == "completed"
        assert wait_job(client, second["job_id"])["status"] == "completed"
    assert len(runtimes) == 1
    assert used[0] is used[1]
    assert runtimes[0].closed
