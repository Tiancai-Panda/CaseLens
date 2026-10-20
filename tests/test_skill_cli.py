import json

from typer.testing import CliRunner

from caselens.cli import app


def test_skill_catalog_command_runs_without_provider_configuration():
    result = CliRunner().invoke(app, ["skills"])
    assert result.exit_code == 0, result.output
    entries = json.loads(result.stdout)["skills"]
    assert {entry["name"] for entry in entries} == {"return-request", "wrong-item", "missing-quantity", "damaged-packaging", "product-issue"}


def test_skill_details_list_resources_without_running_scripts():
    result = CliRunner().invoke(app, ["skills", "--name", "missing-quantity"])
    assert result.exit_code == 0, result.output
    detail = json.loads(result.stdout)
    assert any(path.startswith("scripts/") for path in detail["resources"])
