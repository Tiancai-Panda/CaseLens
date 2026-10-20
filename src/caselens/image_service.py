"""Confined, persistent image transformations; no model or configuration access."""

import base64
import binascii
import hashlib
import io
import json
import math
import re
import tempfile
from pathlib import Path

from PIL import Image

from caselens.assets import AssetStore
from caselens.schemas import AssetRef


class ImageService:
    def __init__(self, root: Path, max_bytes: int = 20 * 1024 * 1024,
                 max_pixels: int = 30_000_000):
        self.root = root.resolve(strict=True)
        self.store = AssetStore(self.root, max_bytes=max_bytes, max_pixels=max_pixels)
        self.directory = self.root / ".image-tools"
        self.directory.mkdir(exist_ok=True)
        if not self.directory.resolve().is_relative_to(self.root):
            raise ValueError("image artifact directory escaped its root")
        for path in self.directory.glob("*.json"):
            try:
                if path.is_symlink() or path.resolve().parent != self.directory.resolve():
                    raise ValueError("manifest path escaped its directory")
                asset = AssetRef.model_validate_json(path.read_text())
                self._validate_paths(asset)
                if path.stem != asset.asset_id:
                    raise ValueError("manifest identity mismatch")
                self.store.assets[asset.asset_id] = asset
            except (OSError, ValueError) as exc:
                raise ValueError("invalid image artifact manifest") from exc

    def _path(self, value: str) -> Path:
        try:
            path = Path(value).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("image path is missing or unsafe") from exc
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError("image path is outside the allowed input root")
        if path.stat().st_size > self.store.max_bytes:
            raise ValueError("image file size exceeds configured limit")
        return path

    def _validate_paths(self, asset: AssetRef) -> None:
        if not re.fullmatch(r"[acdi]_[a-f0-9]{24}", asset.asset_id):
            raise ValueError("invalid asset identity")
        self._path(asset.source_path)
        if asset.derived_path is not None:
            path = self._path(asset.derived_path)
            if path.parent != self.directory.resolve() or path.name != asset.asset_id + ".png":
                raise ValueError("invalid derived artifact path")
        if asset.width <= 0 or asset.height <= 0 or asset.width * asset.height > self.store.max_pixels:
            raise ValueError("image pixel size exceeds configured limit")
        if asset.region is not None:
            self._region(asset.region)

    def _persist(self, asset: AssetRef) -> None:
        self._write(self.directory / (asset.asset_id + ".json"),
                    asset.model_dump_json().encode())
        self.store.assets[asset.asset_id] = asset

    def _write(self, target: Path, data: bytes) -> None:
        if target.parent.resolve() != self.directory.resolve():
            raise ValueError("artifact path escaped its directory")
        with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as temporary:
            temporary.write(data)
            temporary_path = Path(temporary.name)
        try:
            temporary_path.replace(target)
        finally:
            temporary_path.unlink(missing_ok=True)

    def register_image(self, relative_path: str) -> AssetRef:
        """Register only an existing relative image under the configured root."""
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("registration requires a relative path inside the root")
        try:
            asset = self.store.register(self._path(str(self.root / path)))
        except (OSError, RuntimeError) as exc:
            raise ValueError("image path is missing or unsafe") from exc
        self._persist(asset)
        return asset

    def register_assets(self, assets: list[dict]) -> dict:
        """Import the controlling process registry after path/hash validation."""
        if len(assets) > 1000:
            raise ValueError("too many image registrations")
        for record in assets:
            metadata = record.get("asset")
            asset = AssetRef.model_validate(metadata)
            self._validate_paths(asset)
            if asset.parent_asset_id is None:
                registered = self.store.register(self._path(asset.source_path))
                if registered != asset:
                    raise ValueError("input changed or registration metadata mismatch")
                self._persist(registered)
                continue
            parent = self.store.get(asset.parent_asset_id)
            root = self.store.get(asset.root_asset_id)
            if parent.root_asset_id != root.asset_id or asset.source_path != root.source_path:
                raise ValueError("derived image provenance mismatch")
            self._source_unchanged(root)
            encoded = record.get("data")
            if encoded is not None:
                if not isinstance(encoded, str) or len(encoded) > (self.store.max_bytes + 2) // 3 * 4:
                    raise ValueError("derived image byte limit exceeded")
                try:
                    data = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise ValueError("invalid derived image bytes") from exc
                self._validate_data(asset, data)
                asset = asset.model_copy(update={"derived_path": str(self.directory / (asset.asset_id + ".png"))})
                self._write(Path(asset.derived_path), data)
            elif asset.derived_path is None:
                raise ValueError("derived image pixels are missing")
            else:
                self._validate_data(asset, self._path(asset.derived_path).read_bytes())
            self._persist(asset)
        return {"registered": len(assets)}

    def _source_unchanged(self, asset: AssetRef) -> None:
        data = self._path(asset.source_path).read_bytes()
        if hashlib.sha256(data).hexdigest() != asset.content_hash:
            raise ValueError("input changed after registration")

    def _validate_data(self, asset: AssetRef, data: bytes) -> None:
        if len(data) > self.store.max_bytes:
            raise ValueError("image file size exceeds configured limit")
        if hashlib.sha256(data).hexdigest() != asset.content_hash:
            raise ValueError("image content changed after registration")
        image = self.store._load(data)
        if (image.width, image.height) != (asset.width, asset.height):
            raise ValueError("image dimensions do not match metadata")

    def _image(self, asset_id: str) -> tuple[AssetRef, Image.Image]:
        asset = self.store.get(asset_id)
        self._validate_paths(asset)
        root = self.store.get(asset.root_asset_id)
        self._source_unchanged(root)
        path = asset.derived_path or asset.source_path
        data = self._path(path).read_bytes()
        self._validate_data(asset, data)
        return asset, self.store._load(data)

    @staticmethod
    def _region(region: list[float]) -> tuple[float, float, float, float]:
        if (len(region) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                   or not math.isfinite(v) or not 0 <= v <= 1 for v in region)):
            raise ValueError("crop region must contain four finite normalized coordinates")
        x0, y0, x1, y1 = region
        if x0 >= x1 or y0 >= y1:
            raise ValueError("crop must have positive area")
        return x0, y0, x1, y1

    def _derived(self, source: AssetRef, image: Image.Image, operation: str,
                 parameters: dict, region: list[float] | None) -> AssetRef:
        if image.width * image.height > self.store.max_pixels:
            raise ValueError("image pixel size exceeds configured limit")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = buffer.getvalue()
        if len(data) > self.store.max_bytes:
            raise ValueError("image file size exceeds configured limit")
        identity = json.dumps([source.asset_id, operation, parameters], sort_keys=True,
                              separators=(",", ":"), allow_nan=False)
        asset_id = "i_" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        path = self.directory / (asset_id + ".png")
        asset = AssetRef(asset_id=asset_id, root_asset_id=source.root_asset_id,
                         parent_asset_id=source.asset_id, source_path=source.source_path,
                         derived_path=str(path), content_hash=hashlib.sha256(data).hexdigest(),
                         mime_type="image/png", width=image.width, height=image.height,
                         region=region, transforms=[*source.transforms,
                             {"operation": operation, "parameters": parameters}])
        self._write(path, data)
        self._persist(asset)
        return asset

    def crop_image(self, asset_id: str, region: list[float]) -> AssetRef:
        x0, y0, x1, y1 = self._region(region)
        source, image = self._image(asset_id)
        box = (round(x0 * image.width), round(y0 * image.height),
               round(x1 * image.width), round(y1 * image.height))
        if box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError("crop is smaller than one pixel")
        # Use actual rounded pixel bounds when mapping back to original coordinates.
        coords = [box[0] / image.width, box[1] / image.height,
                  box[2] / image.width, box[3] / image.height]
        rotated = any(t.get("operation") == "rotate_image" for t in source.transforms)
        mapped = None
        if not rotated:
            rx0, ry0, rx1, ry1 = source.region or [0, 0, 1, 1]
            a, b, c, d = coords
            mapped = [rx0 + a * (rx1 - rx0), ry0 + b * (ry1 - ry0),
                      rx0 + c * (rx1 - rx0), ry0 + d * (ry1 - ry0)]
        return self._derived(source, image.crop(box), "crop_image", {"region": region}, mapped)

    def resize_image(self, asset_id: str, width: int, height: int) -> AssetRef:
        if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0
                or width * height > self.store.max_pixels):
            raise ValueError("resize dimensions exceed configured pixel limit")
        source, image = self._image(asset_id)
        resized = image.resize((width, height), resample=Image.Resampling.LANCZOS)
        return self._derived(source, resized, "resize_image",
                             {"width": width, "height": height}, source.region)

    def rotate_image(self, asset_id: str, angle: float) -> AssetRef:
        if (isinstance(angle, bool) or not isinstance(angle, (int, float))
                or not math.isfinite(angle) or not -360 <= angle <= 360):
            raise ValueError("rotation angle must be finite and within [-360, 360]")
        source, image = self._image(asset_id)
        radians = math.radians(angle)
        cosine, sine = abs(math.cos(radians)), abs(math.sin(radians))
        # Exact right-angle rotations do not expand by rounding margins.
        if angle % 90 == 0:
            pixels = image.width * image.height
        else:
            pixels = ((math.ceil(image.width * cosine + image.height * sine) + 2)
                      * (math.ceil(image.width * sine + image.height * cosine) + 2))
        if pixels > self.store.max_pixels:
            raise ValueError("rotated image exceeds configured pixel limit")
        return self._derived(source, image.rotate(angle, expand=True), "rotate_image",
                             {"angle": angle}, None)
