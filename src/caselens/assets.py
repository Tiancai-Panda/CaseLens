"""Validate image inputs and track immutable crop provenance."""

import hashlib
import io
import math
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from caselens.schemas import AssetRef


class AssetStore:
    def __init__(self, root: Path, max_bytes: int = 20 * 1024 * 1024, max_pixels: int = 30_000_000):
        self.root = root.resolve()
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels
        self.assets: dict[str, AssetRef] = {}
        self._crops: dict[str, bytes] = {}

    def _load(self, data: bytes) -> Image.Image:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if image.format not in {"PNG", "JPEG", "WEBP"}:
                        raise ValueError("supported image formats: PNG, JPEG, WEBP")
                    if image.width * image.height > self.max_pixels:
                        raise ValueError("image pixel size exceeds configured limit")
                    image.load()
                    return ImageOps.exif_transpose(image).convert("RGB")
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError("invalid or unsafe image") from exc

    def register(self, path: Path) -> AssetRef:
        path = path.resolve(strict=True)
        if not path.is_relative_to(self.root):
            raise ValueError("image path is outside the allowed input root")
        if not path.is_file() or path.stat().st_size > self.max_bytes:
            raise ValueError("image file size exceeds configured limit")
        data = path.read_bytes()
        image = self._load(data)
        digest = hashlib.sha256(data).hexdigest()
        asset_id = "a_" + hashlib.sha256((digest + str(path)).encode()).hexdigest()[:24]
        with Image.open(io.BytesIO(data)) as original:
            mime = Image.MIME[original.format]
        asset = AssetRef(asset_id=asset_id, root_asset_id=asset_id, content_hash=digest,
                         source_path=str(path), mime_type=mime, width=image.width, height=image.height)
        self.assets[asset_id] = asset
        return asset

    def get(self, asset_id: str) -> AssetRef:
        if asset_id not in self.assets:
            raise ValueError("unknown asset_id")
        return self.assets[asset_id]

    def data(self, asset_id: str) -> bytes:
        asset = self.get(asset_id)
        if asset.parent_asset_id:
            original = self.get(asset.root_asset_id)
            source = Path(original.source_path).resolve(strict=True)
            if (not source.is_relative_to(self.root) or source.stat().st_size > self.max_bytes
                    or hashlib.sha256(source.read_bytes()).hexdigest() != original.content_hash):
                raise ValueError("original image changed")
        if asset_id in self._crops:
            return self._crops[asset_id]
        if asset.derived_path:
            path = Path(asset.derived_path).resolve(strict=True)
            if not path.is_relative_to(self.root / ".image-tools") or path.stat().st_size > self.max_bytes:
                raise ValueError("derived artifact escaped image root")
            root = self.get(asset.root_asset_id)
            source = Path(root.source_path).resolve(strict=True)
            if not source.is_relative_to(self.root) or hashlib.sha256(source.read_bytes()).hexdigest() != root.content_hash:
                raise ValueError("original image changed")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != asset.content_hash:
                raise ValueError("derived artifact changed")
            return data
        path = Path(asset.source_path).resolve(strict=True)
        if not path.is_relative_to(self.root) or path.stat().st_size > self.max_bytes:
            raise ValueError("input changed or escaped its root")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != asset.content_hash:
            raise ValueError("input changed after registration")
        return data

    def crop(self, asset_id: str, region: list[float]) -> AssetRef:
        if len(region) != 4 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in region):
            raise ValueError("crop region must contain four normalized coordinates")
        x0, y0, x1, y1 = region
        if x0 >= x1 or y0 >= y1:
            raise ValueError("crop must have positive area")
        source = self.get(asset_id)
        image = self._load(self.data(asset_id))
        box = (round(x0 * image.width), round(y0 * image.height), round(x1 * image.width), round(y1 * image.height))
        if box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError("crop is smaller than one pixel")
        cropped = image.crop(box)
        buffer = io.BytesIO()
        cropped.save(buffer, format="PNG")
        data = buffer.getvalue()
        if len(data) > self.max_bytes:
            raise ValueError("crop file size exceeds configured limit")
        digest = hashlib.sha256(data).hexdigest()
        crop_id = "c_" + hashlib.sha256((asset_id + str(region)).encode()).hexdigest()[:24]
        if source.region:
            rx0, ry0, rx1, ry1 = source.region
            mapped = [rx0 + x0 * (rx1-rx0), ry0 + y0 * (ry1-ry0), rx0 + x1 * (rx1-rx0), ry0 + y1 * (ry1-ry0)]
        else:
            mapped = region
        asset = AssetRef(asset_id=crop_id, root_asset_id=source.root_asset_id,
                         parent_asset_id=asset_id, content_hash=digest, source_path=source.source_path,
                         mime_type="image/png", width=cropped.width, height=cropped.height, region=mapped)
        self.assets[crop_id] = asset
        self._crops[crop_id] = data
        return asset
