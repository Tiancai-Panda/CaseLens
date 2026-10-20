from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from caselens.assets import AssetStore
from caselens.schemas import ChoicePrediction, VisualObservation
from caselens.store import EvidenceStore


def image(path: Path, size=(100, 80)):
    Image.new("RGB", size, "white").save(path)
    return path


def test_crop_preserves_root_source_and_has_correct_size(tmp_path):
    assets = AssetStore(tmp_path)
    original = assets.register(image(tmp_path / "中文商品.png"))
    crop = assets.crop(original.asset_id, [0.2, 0.25, 0.8, 0.75])
    assert (crop.width, crop.height) == (60, 40)
    assert crop.root_asset_id == original.asset_id
    assert crop.source_path == str(tmp_path / "中文商品.png")
    assert crop.region == [0.2, 0.25, 0.8, 0.75]


@pytest.mark.parametrize("region", [[0, 0, 0, 1], [-0.1, 0, 1, 1], [0, 0, 1.1, 1]])
def test_invalid_crop_is_rejected(tmp_path, region):
    assets = AssetStore(tmp_path)
    original = assets.register(image(tmp_path / "photo.png"))
    with pytest.raises(ValueError):
        assets.crop(original.asset_id, region)


def test_asset_cannot_escape_input_root(tmp_path):
    root = tmp_path / "inbox"
    root.mkdir()
    outside = image(tmp_path / "private.png")
    with pytest.raises(ValueError, match="root"):
        AssetStore(root).register(outside)


def test_corrupt_and_oversized_images_fail_before_model_call(tmp_path):
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    assets = AssetStore(tmp_path, max_bytes=30)
    with pytest.raises(ValueError):
        assets.register(broken)
    with pytest.raises(ValueError, match="size"):
        assets.register(image(tmp_path / "large.png"))


@pytest.mark.parametrize("probabilities", [{"a": 0.9, "b": 0.9}, {"a": -0.1, "b": 1.1}, {"a": float("nan")}])
def test_invalid_probability_distribution_is_rejected(probabilities):
    with pytest.raises(ValidationError):
        ChoicePrediction(choice="a", probabilities=probabilities)


def test_cache_changes_when_model_prompt_or_question_changes(tmp_path):
    with EvidenceStore(tmp_path / "cache.sqlite3") as cache:
        observation = VisualObservation(facts=["纸箱右下角撕裂"], text=[], unknowns=[])
        key = cache.key("imagehash", "vlm-a", "prompt-v1", "inspect box")
        cache.put(key, observation)
        assert cache.get(key).facts == ["纸箱右下角撕裂"]
        for model, prompt, question in [("vlm-b", "prompt-v1", "inspect box"), ("vlm-a", "prompt-v2", "inspect box"), ("vlm-a", "prompt-v1", "read label")]:
            assert cache.get(cache.key("imagehash", model, prompt, question)) is None
