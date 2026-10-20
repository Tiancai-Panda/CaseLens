import json
from pathlib import Path

import pytest

from caselens.skills import SkillRegistry


def test_catalog_discovers_all_scenario_bundles_with_manifest_metadata():
    catalog = SkillRegistry().catalog()
    assert [item["name"] for item in catalog] == [
        "damaged-packaging", "missing-quantity", "product-issue", "return-request", "wrong-item"
    ]
    assert all(item["description"] and item["version"] for item in catalog)
    assert all(isinstance(item["issues"], list) for item in catalog)


def test_select_routes_multiple_issues_and_returns_complete_skill_content():
    selected = SkillRegistry().select(["missing_quantity", "wrong_item"])
    assert [item["name"] for item in selected] == ["missing-quantity", "wrong-item"]
    assert all(item["version"] == "1.0.0" for item in selected)
    assert all(item["content"].startswith("---") for item in selected)


def test_select_deduplicates_names_and_unknown_issue_is_ignored():
    selected = SkillRegistry().select(["missing-quantity", "missing_quantity", "unknown"])
    assert [item["name"] for item in selected] == ["missing-quantity"]


def test_activation_and_resource_read_are_allowlisted_and_bounded():
    registry = SkillRegistry()
    activated = registry.activate("missing-quantity")
    assert activated["name"] == "missing-quantity"
    assert "references/split-shipment.md" in activated["resources"]
    resource = registry.read_resource("missing-quantity", "references/split-shipment.md")
    assert resource["name"] == "missing-quantity"
    assert "in transit" in resource["content"]
    for path in ("../return-request/SKILL.md", "/etc/passwd", "manifest.json"):
        with pytest.raises(ValueError):
            registry.read_resource("missing-quantity", path)
    with pytest.raises(ValueError):
        registry.activate("not-a-bundle")


def test_malformed_bundle_metadata_is_rejected(tmp_path: Path):
    bundle = tmp_path / "bad-bundle"
    bundle.mkdir()
    (bundle / "SKILL.md").write_text(
        "---\nname: wrong-name\ndescription: x\n---\nbody\n", encoding="utf-8"
    )
    (bundle / "manifest.json").write_text(
        json.dumps({"version": "1.0.0", "intent": "bad", "resources": []}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="name"):
        SkillRegistry(tmp_path).catalog()


@pytest.mark.parametrize("data,status,missing_count", [
    ({"expected": 3, "received": 2, "pending": 1, "order_evidence": True,
      "complete_photo": True, "evidence_ids": ["order-1", "photo-1", "tracking-1"]},
     "pending_shipment", 0),
    ({"expected": 3, "received": 2, "pending": 0, "order_evidence": True,
      "complete_photo": True, "evidence_ids": ["order-1", "photo-1"]}, "shortage", 1),
    ({"expected": 3, "received": 2, "pending": 0, "order_evidence": False,
      "complete_photo": True, "evidence_ids": []}, "missing_evidence", 1),
    ({"expected": 3, "received": 3, "pending": 0, "order_evidence": True,
      "complete_photo": True, "evidence_ids": ["order-1", "photo-1"]}, "consistent", 0),
    ({"expected": 3, "received": 4, "pending": 0, "order_evidence": True,
      "complete_photo": True, "evidence_ids": ["order-1", "photo-1"]}, "conflict", 0),
])
def test_quantity_checker_returns_conservative_decision(data, status, missing_count):
    result = SkillRegistry().check_quantity(data)
    assert result["status"] == status
    assert result["missing_count"] == missing_count
    assert result["evidence_ids"] == data["evidence_ids"]


@pytest.mark.parametrize("key,value", [
    ("expected", -1), ("expected", 10**30), ("received", True),
    ("order_evidence", 1), ("evidence_ids", ["", 3]),
])
def test_quantity_checker_rejects_unsafe_counts_and_flags(key, value):
    base = {"expected": 1, "received": 0, "pending": 0, "order_evidence": True,
            "complete_photo": True, "evidence_ids": ["photo-1"]}
    with pytest.raises(ValueError):
        SkillRegistry().check_quantity({**base, key: value})


def fixture_bundle(root, *, resources=None, header=None, manifest=None):
    bundle = root / "fixture"
    bundle.mkdir()
    (bundle / "SKILL.md").write_text(
        header or "---\nname: fixture\ndescription: Fixture scenario\n---\nInstructions\n",
        encoding="utf-8",
    )
    (bundle / "manifest.json").write_text(
        manifest or json.dumps({
            "name": "fixture", "version": "1.0.0", "issues": ["fixture"],
            "resources": resources or [],
        }),
        encoding="utf-8",
    )
    return bundle


@pytest.mark.parametrize("kind", ["yaml", "manifest", "resource"])
def test_duplicate_metadata_and_resource_names_are_rejected(tmp_path, kind):
    kwargs = {}
    if kind == "yaml":
        kwargs["header"] = (
            "---\nname: fixture\nname: fixture\ndescription: Duplicate\n---\nInstructions\n"
        )
    elif kind == "manifest":
        kwargs["manifest"] = (
            '{"name":"fixture","version":"1.0.0","version":"2.0.0",'
            '"issues":["fixture"],"resources":[]}'
        )
    else:
        kwargs["resources"] = ["reference.md", "reference.md"]
    fixture_bundle(tmp_path, **kwargs)
    with pytest.raises(ValueError):
        SkillRegistry(tmp_path)


def test_bundle_symlink_escape_is_rejected(tmp_path):
    bundle = fixture_bundle(tmp_path, resources=["reference.md"])
    (bundle / "reference.md").symlink_to("/etc/passwd")
    with pytest.raises(ValueError):
        SkillRegistry(tmp_path)


def test_resource_symlink_inserted_after_discovery_is_rejected(tmp_path):
    bundle = fixture_bundle(tmp_path, resources=["reference.md"])
    reference = bundle / "reference.md"
    reference.write_text("Trusted reference", encoding="utf-8")
    registry = SkillRegistry(tmp_path)
    reference.unlink()
    reference.symlink_to("/etc/passwd")
    with pytest.raises(ValueError):
        registry.read_resource("fixture", "reference.md")


def test_bundle_directory_replaced_by_symlink_is_rejected(tmp_path):
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    bundle = fixture_bundle(catalog, resources=["reference.md"])
    (bundle / "reference.md").write_text("Original", encoding="utf-8")
    registry = SkillRegistry(catalog)
    relocated = tmp_path / "outside"
    bundle.rename(relocated)
    bundle.symlink_to(relocated, target_is_directory=True)
    with pytest.raises(ValueError):
        registry.read_resource("fixture", "reference.md")


def test_large_text_resource_is_bounded_on_read(tmp_path):
    bundle = fixture_bundle(tmp_path, resources=["reference.md"])
    (bundle / "reference.md").write_text("x" * 65537, encoding="utf-8")
    registry = SkillRegistry(tmp_path)
    with pytest.raises(ValueError):
        registry.read_resource("fixture", "reference.md")


def test_bundle_file_count_is_bounded(tmp_path):
    bundle = fixture_bundle(tmp_path)
    for number in range(65):
        (bundle / f"unused-{number}.md").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        SkillRegistry(tmp_path)


def test_custom_catalog_cannot_replace_shipped_executable(tmp_path):
    fixture_bundle(tmp_path)
    (tmp_path / "check_quantity.py").write_text(
        "raise AssertionError('untrusted helper executed')", encoding="utf-8"
    )
    result = SkillRegistry(tmp_path).check_quantity({
        "expected": 2, "received": 2, "pending": 0, "order_evidence": True,
        "complete_photo": True, "evidence_ids": ["order", "photo"],
    })
    assert result["status"] == "consistent"


@pytest.mark.parametrize("field", ["expected", "received", "pending"])
def test_unknown_quantity_never_becomes_shortage_or_numeric_missing_count(field):
    data = {"expected": 3, "received": 2, "pending": 0, "order_evidence": True,
            "complete_photo": True, "evidence_ids": ["order", "photo"]}
    result = SkillRegistry().check_quantity({**data, field: None})
    assert result["status"] == "missing_evidence"
    assert result["missing_count"] is None


@pytest.mark.parametrize("change", [
    {"order_evidence": False}, {"complete_photo": False}, {"evidence_ids": []},
])
def test_counts_alone_never_prove_shortage(change):
    data = {"expected": 3, "received": 2, "pending": 0, "order_evidence": True,
            "complete_photo": True, "evidence_ids": ["order", "photo"]}
    assert SkillRegistry().check_quantity({**data, **change})["status"] == "missing_evidence"
