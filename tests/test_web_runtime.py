from fastapi.testclient import TestClient
from test_web_api import image_payload, wait_job

from caselens.web import create_app


def test_web_serves_actual_derived_image_and_runtime_diagnostics(tmp_path):
    payload = image_payload()
    # A 1px crop is invalid, so use the existing replay fixture at realistic dimensions.
    import base64
    import io

    from PIL import Image
    stream = io.BytesIO()
    Image.new("RGB", (80, 60), "white").save(stream, format="PNG")
    payload["data_base64"] = base64.b64encode(stream.getvalue()).decode()
    with TestClient(create_app(state_dir=tmp_path / "state")) as client:
        job = client.post("/api/jobs", json={"text": "包装模糊", "order_id": "WEB", "replay": "blurred", "images": [payload]}).json()
        result = wait_job(client, job["job_id"])["result"]
        assert result["status"] == "completed"
        evidence = next(e for e in result["evidence"] if e["asset"]["parent_asset_id"])
        response = client.get(f"/api/cases/{result['case_id']}/images/{evidence['asset']['asset_id']}")
        assert response.status_code == 200
        with Image.open(io.BytesIO(response.content)) as derived:
            assert derived.size == (40, 30)
        events = client.get(f"/api/jobs/{job['job_id']}/events").json()["events"]
        assert any(e["type"] == "graph_node" for e in events)
        assert any(e["type"] == "skills_loaded" for e in events)
        assert any(e["type"] == "context_prepared" for e in events)
        assert client.get("/api/skills").status_code == 200
        assert result["runtime"] == "langgraph"
