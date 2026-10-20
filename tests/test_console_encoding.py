import json

import pytest
from PIL import Image
from typer.testing import CliRunner

from caselens.cli import app


@pytest.mark.parametrize("encoding", ["gbk", "cp1252", "utf-8"])
@pytest.mark.parametrize("command", ["inspect", "batch"])
def test_json_stdout_survives_legacy_shell_decoding(tmp_path, encoding, command):
    inbox = tmp_path / "商品目录"
    inbox.mkdir()
    photo = inbox / "照片.jpg"
    Image.new("RGB", (32, 32), "white").save(photo)
    args = [command, str(photo if command == "inspect" else inbox), "--text", "包装破损",
            "--order-id", "ORDER-1", "--replay", "clear", "--state-dir", str(tmp_path / "state")]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    # Windows PowerShell can decode native UTF-8 stdout with the console's legacy code page.
    decoded = result.stdout.encode("utf-8").decode(encoding, errors="replace")
    payload = json.loads(decoded)
    run = payload if command == "inspect" else payload["result"]
    assert run["intent"]["issues"] == ["packaging_issue"]
    assert "补偿" in run["user_message"]
    if command == "batch":
        assert "商品目录" in payload["path"]
