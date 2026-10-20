"""Offline tests use the actual MCP stdio transport and persistent pixels."""

import importlib
import io
import math

import pytest
from PIL import Image

from caselens.assets import AssetStore


def client_type():
    assert importlib.util.find_spec("caselens.image_mcp"), "image MCP client is missing"
    return importlib.import_module("caselens.image_mcp").ImageMCPClient


@pytest.fixture
def assets(tmp_path):
    image = Image.new("RGB", (80, 40), "red")
    image.paste("blue", (40, 0, 80, 40))
    image.save(tmp_path / "input.png")
    store = AssetStore(tmp_path)
    store.register(tmp_path / "input.png")
    return store


@pytest.mark.asyncio
async def test_real_stdio_discovery_transforms_and_restart(assets):
    client_cls = client_type()
    source = next(iter(assets.assets.values()))
    original = assets.data(source.asset_id)
    async with client_cls(assets) as client:
        schemas = client.schemas()
        assert {t["function"]["name"] for t in schemas} == {"crop_image", "resize_image", "rotate_image"}
        crop = await client.call("crop_image", {"asset_id": source.asset_id, "region": [0.5, 0, 1, 1]})
        assert (crop.width, crop.height) == (40, 40)
        assert crop.region == [0.5, 0, 1, 1]
        assert Image.open(io.BytesIO(assets.data(crop.asset_id))).getpixel((0, 0)) == (0, 0, 255)
        resized = await client.call("resize_image", {"asset_id": crop.asset_id, "width": 20, "height": 10})
        assert (resized.width, resized.height) == (20, 10)
        rotated = await client.call("rotate_image", {"asset_id": resized.asset_id, "angle": 90})
        assert (rotated.width, rotated.height) == (10, 20)
        assert rotated.root_asset_id == source.asset_id
        assert rotated.parent_asset_id == resized.asset_id
        assert rotated.region is None
        assert [t["operation"] for t in rotated.transforms] == ["crop_image", "resize_image", "rotate_image"]
        after_rotation = await client.call("crop_image", {"asset_id": rotated.asset_id, "region": [0, 0, 1, 0.5]})
        assert after_rotation.region is None
        saved = assets.data(rotated.asset_id)
    assert assets.data(source.asset_id) == original
    fresh = AssetStore(assets.root)
    fresh.register(assets.root / "input.png")
    async with client_cls(fresh) as client:
        restored = await client.call("resize_image", {"asset_id": rotated.asset_id, "width": 10, "height": 20})
        assert fresh.data(restored.asset_id) == saved


@pytest.mark.asyncio
@pytest.mark.parametrize("name,arguments", [
    ("crop_image", {"region": [0, 0, 0, 1]}),
    ("crop_image", {"region": [-0.1, 0, 1, 1]}),
    ("crop_image", {"region": [0, 0, math.nan, 1]}),
    ("crop_image", {"region": [0, 0, 0.001, 1]}),
    ("resize_image", {"width": 0, "height": 10}),
    ("resize_image", {"width": 100000, "height": 100000}),
    ("rotate_image", {"angle": 361}),
    ("rotate_image", {"angle": math.inf}),
])
async def test_invalid_transform_is_explicit_error(assets, name, arguments):
    async with client_type()(assets) as client:
        with pytest.raises(ValueError):
            await client.call(name, {"asset_id": next(iter(assets.assets)), **arguments})


@pytest.mark.asyncio
async def test_unknown_id_and_closed_client_do_not_fallback(assets):
    client = client_type()(assets)
    async with client:
        with pytest.raises(ValueError, match="unknown"):
            await client.call("rotate_image", {"asset_id": "unknown", "angle": 90})
    with pytest.raises(RuntimeError, match="connected"):
        await client.call("crop_image", {"asset_id": next(iter(assets.assets)), "region": [0, 0, 1, 1]})


@pytest.mark.asyncio
async def test_changed_source_is_rejected(assets):
    async with client_type()(assets) as client:
        Image.new("RGB", (80, 40), "green").save(assets.root / "input.png")
        with pytest.raises(ValueError, match="changed"):
            await client.call("rotate_image", {"asset_id": next(iter(assets.assets)), "angle": 90})


@pytest.mark.asyncio
async def test_existing_in_memory_crop_can_cross_protocol(assets):
    original = next(iter(assets.assets))
    crop = assets.crop(original, [0.5, 0, 1, 1])
    async with client_type()(assets) as client:
        resized = await client.call("resize_image", {"asset_id": crop.asset_id, "width": 10, "height": 10})
        assert Image.open(io.BytesIO(assets.data(resized.asset_id))).getpixel((0, 0)) == (0, 0, 255)


def test_external_registration_is_relative_and_confined(assets, tmp_path):
    assert importlib.util.find_spec("caselens.image_service"), "image service is missing"
    service = importlib.import_module("caselens.image_service").ImageService(assets.root)
    source = service.register_image("input.png")
    assert source.asset_id == next(iter(assets.assets))
    for path in (str(assets.root / "input.png"), "../input.png", "missing.png"):
        with pytest.raises(ValueError):
            service.register_image(path)
    outside = tmp_path.parent / "outside.png"
    Image.new("RGB", (10, 10)).save(outside)
    (assets.root / "escape.png").symlink_to(outside)
    with pytest.raises(ValueError):
        service.register_image("escape.png")


def server_process():
    """Identify this test's actual stdio child without production test hooks."""
    import os
    from pathlib import Path

    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            status = (directory / "status").read_text()
            parent = next(line for line in status.splitlines() if line.startswith("PPid:"))
            command = (directory / "cmdline").read_bytes()
            if int(parent.split()[1]) == os.getpid() and b"caselens.image_server" in command:
                return int(directory.name)
        except (OSError, StopIteration):
            continue
    raise AssertionError("real image MCP subprocess was not found")


@pytest.mark.asyncio
async def test_real_subprocess_does_not_inherit_model_secrets(assets, monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-never-forward")
    monkeypatch.setenv("JEV_API_KEY", "test-secret-never-forward")
    monkeypatch.setenv("CUSTOM_PROVIDER_TOKEN", "test-secret-never-forward")
    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    async with client_type()(assets):
        environment = (Path("/proc") / str(server_process()) / "environ").read_bytes()
        assert b"test-secret-never-forward" not in environment
        assert b"/untrusted" not in environment


@pytest.mark.asyncio
async def test_disconnected_server_is_explicit_transport_error(assets):
    import os
    import signal

    async with client_type()(assets) as client:
        os.kill(server_process(), signal.SIGKILL)
        with pytest.raises(RuntimeError, match="transport"):
            await client.call("rotate_image", {"asset_id": next(iter(assets.assets)), "angle": 90})


@pytest.mark.asyncio
async def test_frozen_server_times_out_without_local_fallback(assets):
    import os
    import signal

    async with client_type()(assets) as client:
        pid = server_process()
        os.kill(pid, signal.SIGSTOP)
        client.timeout = 0.15
        try:
            with pytest.raises(RuntimeError, match="timed out"):
                await client.call("rotate_image", {"asset_id": next(iter(assets.assets)), "angle": 90})
        finally:
            os.kill(pid, signal.SIGCONT)
    assert len(assets.assets) == 1


@pytest.mark.asyncio
async def test_task_cancellation_closes_stdio_subprocess(assets):
    import asyncio
    import os
    import signal
    from pathlib import Path

    ready = asyncio.Event()
    holder = {}

    async def run():
        async with client_type()(assets) as client:
            holder["pid"] = server_process()
            os.kill(holder["pid"], signal.SIGSTOP)
            ready.set()
            await client.call("rotate_image", {"asset_id": next(iter(assets.assets)), "angle": 90})

    task = asyncio.create_task(run())
    await asyncio.wait_for(ready.wait(), timeout=5)
    task.cancel()
    os.kill(holder["pid"], signal.SIGCONT)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert not (Path("/proc") / str(holder["pid"])).exists()


def test_manifest_symlink_cannot_read_outside_root(assets, tmp_path):
    import importlib

    service_cls = importlib.import_module("caselens.image_service").ImageService
    service_cls(assets.root)
    outside = tmp_path.parent / "external.json"
    outside.write_text(next(iter(assets.assets.values())).model_dump_json())
    asset_id = next(iter(assets.assets))
    (assets.root / ".image-tools" / (asset_id + ".json")).symlink_to(outside)
    with pytest.raises(ValueError, match="manifest"):
        service_cls(assets.root)
