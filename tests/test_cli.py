import json

import pytest
from PIL import Image
from typer.testing import CliRunner

from caselens.cli import app

runner = CliRunner()


def test_demo_runs_without_keys_and_stdout_is_pure_json(tmp_path, monkeypatch):
    monkeypatch.delenv("VLM_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    result = runner.invoke(app, ["demo", "--scenario", "blurred", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["mode"] == "replay"
    assert payload["status"] == "completed"
    assert any(step["call"]["name"] == "crop_image" for step in payload["steps"])
    report = (tmp_path / "blurred" / "report.md").read_text(encoding="utf-8")
    assert "replay" in report
    assert payload["evidence"][0]["evidence_id"] in report


def test_analyze_replay_returns_evidence_for_a_chinese_path(tmp_path):
    photo = tmp_path / "包装照片.png"
    Image.new("RGB", (50, 50), "white").save(photo)
    result = runner.invoke(app, ["analyze", str(photo), "--replay", "clear", "--state-dir", str(tmp_path / "state")])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["mode"] == "replay"
    assert payload["evidence"][0]["asset"]["source_path"].endswith("包装照片.png")


def test_replay_scenarios_do_not_reuse_each_others_observations(tmp_path):
    photo = tmp_path / "photo.png"
    Image.new("RGB", (50, 50), "white").save(photo)
    results = []
    for scenario in ["clear", "missing"]:
        result = runner.invoke(app, ["analyze", str(photo), "--replay", scenario,
                                     "--state-dir", str(tmp_path / "state")])
        assert result.exit_code == 0, result.output
        results.append(json.loads(result.stdout))
    assert results[0]["evidence"][0]["observation"]["facts"] == ["纸箱右下角有可见撕裂"]
    assert results[1]["evidence"][0]["observation"]["facts"] == ["可见外包装"]


def test_repeated_blurred_demo_preserves_crop_observation(tmp_path):
    for _ in range(2):
        result = runner.invoke(app, ["demo", "--scenario", "blurred", "--output-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["status"] == "completed"
        assert payload["evidence"][1]["observation"]["facts"] == ["局部放大后可见纸箱边缘撕裂"]


def test_live_missing_config_exits_two_without_network(tmp_path, monkeypatch):
    monkeypatch.delenv("CASELENS_VLM_BASE_URL", raising=False)
    monkeypatch.delenv("CASELENS_VLM_MODEL", raising=False)
    photo = tmp_path / "photo.png"
    Image.new("RGB", (50, 50), "white").save(photo)
    result = runner.invoke(app, ["inspect", str(photo)])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "configuration"


def test_bad_config_does_not_echo_inline_secrets(tmp_path):
    config = tmp_path / "bad.json"
    config.write_text('{"vlm":{"api_key":"do-not-leak","base_url":"invalid"}}', encoding="utf-8")
    result = runner.invoke(app, ["doctor", "--config", str(config)])
    assert result.exit_code == 2
    assert "do-not-leak" not in result.output


def test_doctor_accepts_inline_keys_and_reports_only_source(tmp_path, monkeypatch):
    for name in ["VLM_API_KEY", "CONTROLLER_API_KEY", "TYPESAFE_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({role: {"api_key": "sk-test-private"} for role in ["vlm", "controller", "jev"]}), encoding="utf-8")
    result = runner.invoke(app, ["doctor", "--config", str(config)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    for role in ["vlm", "controller", "jev"]:
        assert payload[role]["key_present"] is True
        assert payload[role]["key_source"] == "config"
    assert "sk-test-private" not in result.output


def test_corrupt_input_returns_structured_execution_error(tmp_path):
    photo = tmp_path / "broken.png"
    photo.write_bytes(b"corrupted")
    result = runner.invoke(app, ["inspect", str(photo), "--replay", "clear"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["code"] == "invalid_input"


@pytest.mark.parametrize("row", [None, [], 7])
def test_malformed_evaluation_rows_return_json_error(tmp_path, row):
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(json.dumps(row) + "\n", encoding="utf-8")
    result = runner.invoke(app, ["evaluate", str(dataset)])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "invalid_dataset"


@pytest.mark.parametrize("command", ["analyze", "classify", "inspect", "batch", "chat", "doctor", "demo", "evaluate"])
def test_command_help_is_available(command):
    assert runner.invoke(app, [command, "--help"]).exit_code == 0
