import json

from PIL import Image
from typer.testing import CliRunner

from caselens.cli import app

runner = CliRunner()


def test_customer_cli_create_resume_query_and_user_output(tmp_path):
    photo = tmp_path / "无关照片.png"
    Image.new("RGB", (80, 80), "white").save(photo)
    state = str(tmp_path / "state")
    first = runner.invoke(app, ["inspect", str(photo), "--text", "包装破损", "--order-id", "O-1", "--replay", "irrelevant", "--state-dir", state])
    assert first.exit_code == 0, first.output
    result = json.loads(first.stdout)
    assert result["status"] == "needs_review"
    assert "重新拍摄" in result["user_message"]
    case_id = result["case_id"]
    new_photo = tmp_path / "清晰照片.png"
    Image.new("RGB", (80, 80), "blue").save(new_photo)
    second = runner.invoke(app, ["resume", case_id, "--image", str(new_photo), "--replace-asset", result["evidence"][0]["asset"]["root_asset_id"], "--text", "已经补拍", "--replay", "clear", "--state-dir", state, "--format", "user"])
    assert second.exit_code == 0, second.output
    assert "补偿" in second.stdout
    assert "建议不等于执行" in second.stdout
    query = runner.invoke(app, ["case", case_id, "--state-dir", state, "--history"])
    assert query.exit_code == 0, query.output
    assert len(json.loads(query.stdout)["history"]) == 2


def test_chat_keeps_same_case_while_supplementing_metadata(tmp_path):
    photo = tmp_path / "包装.png"
    Image.new("RGB", (80, 80), "white").save(photo)
    result = runner.invoke(app, ["chat", "--replay", "clear", "--state-dir", str(tmp_path / "state")], input=f"{photo}\n包装破了\n\n\n补充订单编号\nO-1\n\n")
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert len(rows) == 2
    assert rows[0]["case_id"] == rows[1]["case_id"]
    assert rows[0]["status"] == "needs_review"
    assert rows[1]["status"] == "completed"


def test_chat_keeps_case_after_human_review_result(tmp_path):
    photo = tmp_path / "包装.png"
    Image.new("RGB", (80, 80), "white").save(photo)
    result = runner.invoke(app, ["chat", "--replay", "irrelevant", "--state-dir", str(tmp_path / "state")], input=f"{photo}\n包装破损\n\n\n补充说明\n继续\n\n")
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert len(rows) == 2
    assert rows[0]["case_id"] == rows[1]["case_id"]


def test_doctor_reports_failed_stage_without_provider_body(tmp_path, monkeypatch):
    from caselens.errors import ClientError
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"vlm": {"base_url": "https://provider.test/v1", "model": "v", "api_key": "private-test"}, "jev": {"api_key": "jev-test"}}), encoding="utf-8")
    async def rejected(self, data, mime_type, *, question):
        raise ClientError("invalid_request", "provider rejected test request")
    from caselens.clients import VLMClient
    monkeypatch.setattr(VLMClient, "analyze", rejected)
    result = runner.invoke(app, ["doctor", "--config", str(config), "--probe"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["stage"] == "vlm"
    assert "private-test" not in result.stdout
