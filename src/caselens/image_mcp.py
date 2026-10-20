"""Async lifecycle for actual stdio MCP discovery and image transformations."""

import asyncio
import base64
import copy
import hashlib
import json
import os
import sys
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from caselens.assets import AssetStore
from caselens.schemas import AssetRef

IMAGE_TOOLS = frozenset({"crop_image", "resize_image", "rotate_image"})


class ImageMCPClient:
    def __init__(self, assets: AssetStore, timeout: float = 15.0):
        if timeout <= 0:
            raise ValueError("MCP timeout must be positive")
        self.assets = assets
        self.timeout = timeout
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._schemas: list[dict] = []
        self._registered: set[str] = set()

    def _environment(self) -> dict[str, str]:
        # No inherited provider keys, config variables, or arbitrary Python search paths.
        safe = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT", "WINDIR",
                "COMSPEC", "PATHEXT", "TEMP", "TMP")
        environment = {key: os.environ[key] for key in safe if key in os.environ
                       and not os.environ[key].startswith("()")}
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        environment["PYTHONUNBUFFERED"] = "1"
        return environment

    async def __aenter__(self):
        if self._stack is not None:
            raise RuntimeError("image MCP client is already connected")
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        self._registered = set()
        try:
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "caselens.image_server", "--root", str(self.assets.root),
                      "--max-bytes", str(self.assets.max_bytes),
                      "--max-pixels", str(self.assets.max_pixels)],
                env=self._environment(),
                cwd=str(self.assets.root),
            )
            read, write = await self._stack.enter_async_context(stdio_client(parameters))
            self._session = await self._stack.enter_async_context(
                ClientSession(read, write, read_timeout_seconds=timedelta(seconds=self.timeout)))
            await self._request(self._session.initialize())
            discovered = await self._request(self._session.list_tools())
            self._schemas = [{"type": "function", "function": {
                "name": tool.name, "description": tool.description or "",
                "parameters": tool.inputSchema}}
                for tool in discovered.tools if tool.name in IMAGE_TOOLS]
            if {s["function"]["name"] for s in self._schemas} != IMAGE_TOOLS:
                raise RuntimeError("image MCP server did not expose required tools")
            await self._sync_assets()
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, exc_type, exc, traceback):
        stack, self._stack = self._stack, None
        self._session = None
        self._schemas = []
        if stack is not None:
            try:
                await stack.__aexit__(None, None, None)
            except* (anyio.BrokenResourceError, anyio.ClosedResourceError):
                # A peer killed or stopped during a call can make the SDK's
                # stdout reader report a broken stream while its process is
                # already being terminated. Preserve call/cancellation errors.
                pass

    async def _request(self, coroutine):
        try:
            async with asyncio.timeout(self.timeout):
                return await coroutine
        except TimeoutError as exc:
            raise RuntimeError("image MCP request timed out") from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise RuntimeError("image MCP transport request failed") from exc

    @staticmethod
    def _output(result) -> dict:
        if result.isError:
            message = "image MCP tool rejected its arguments"
            for item in result.content:
                if item.type == "text":
                    message = item.text
                    break
            raise ValueError(message)
        if not isinstance(result.structuredContent, dict):
            raise RuntimeError("image MCP server returned no structured result")
        return result.structuredContent

    async def _sync_assets(self) -> None:
        assert self._session is not None
        records = []
        for asset in self.assets.assets.values():
            if asset.asset_id in self._registered:
                continue
            record = {"asset": asset.model_dump()}
            if asset.parent_asset_id is not None and asset.derived_path is None:
                record["data"] = base64.b64encode(self.assets.data(asset.asset_id)).decode("ascii")
            records.append(record)
        if records:
            self._output(await self._request(self._session.call_tool("register_assets", {"assets": records})))
            self._registered.update(record["asset"]["asset_id"] for record in records)

    def schemas(self) -> list[dict]:
        if self._session is None:
            raise RuntimeError("image MCP client is not connected")
        return copy.deepcopy(self._schemas)

    async def call(self, name: str, arguments: dict) -> AssetRef:
        if self._session is None:
            raise RuntimeError("image MCP client is not connected")
        if name not in IMAGE_TOOLS:
            raise ValueError("unknown image MCP tool")
        try:
            json.dumps(arguments, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("image tool arguments must be finite JSON values") from exc
        await self._sync_assets()
        output = self._output(await self._request(self._session.call_tool(name, arguments)))
        asset = AssetRef.model_validate(output.get("asset"))
        if asset.derived_path is None:
            raise RuntimeError("image MCP returned no persistent artifact")
        try:
            path = Path(asset.derived_path).resolve(strict=True)
            if (not path.is_relative_to(self.assets.root / ".image-tools")
                    or not path.is_file() or path.stat().st_size > self.assets.max_bytes):
                raise ValueError("image MCP artifact escaped its root or exceeded size limit")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != asset.content_hash:
                raise ValueError("image MCP artifact hash mismatch")
            image = self.assets._load(data)
            if (image.width, image.height) != (asset.width, asset.height):
                raise ValueError("image MCP artifact dimensions mismatch")
        except OSError as exc:
            raise RuntimeError("image MCP artifact is unavailable") from exc
        self.assets.assets[asset.asset_id] = asset
        self.assets._crops[asset.asset_id] = data
        self._registered.add(asset.asset_id)
        return asset
