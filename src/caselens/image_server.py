"""Standalone stdio MCP server. stdout is reserved for the MCP protocol."""

import argparse
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from caselens.image_service import ImageService
from caselens.schemas import AssetRef


class AssetOutput(BaseModel):
    asset: AssetRef


class RegistrationOutput(BaseModel):
    registered: int


def create_server(root: Path, max_bytes: int = 20 * 1024 * 1024,
                  max_pixels: int = 30_000_000) -> FastMCP:
    service = ImageService(root, max_bytes=max_bytes, max_pixels=max_pixels)
    server = FastMCP("CaseLens image tools", log_level="WARNING")

    @server.tool()
    def register_image(relative_path: str) -> AssetOutput:
        """Register a PNG/JPEG/WEBP by relative path under the allowed root."""
        return AssetOutput(asset=service.register_image(relative_path))

    @server.tool()
    def register_assets(assets: list[dict]) -> RegistrationOutput:
        """Register validated asset metadata from the controlling CaseLens process."""
        return RegistrationOutput(**service.register_assets(assets))

    @server.tool()
    def crop_image(asset_id: str, region: list[float]) -> AssetOutput:
        """Crop normalized [x0,y0,x1,y1] coordinates; retain original provenance."""
        return AssetOutput(asset=service.crop_image(asset_id, region))

    @server.tool()
    def resize_image(asset_id: str, width: int, height: int) -> AssetOutput:
        """Resize to positive pixel dimensions; resizing creates no visual facts."""
        return AssetOutput(asset=service.resize_image(asset_id, width, height))

    @server.tool()
    def rotate_image(asset_id: str, angle: float) -> AssetOutput:
        """Rotate degrees counterclockwise, expand canvas, record transformed coordinates."""
        return AssetOutput(asset=service.rotate_image(asset_id, angle))

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="CaseLens confined image MCP stdio server")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, default=20 * 1024 * 1024)
    parser.add_argument("--max-pixels", type=int, default=30_000_000)
    args = parser.parse_args()
    if args.max_bytes <= 0 or args.max_pixels <= 0:
        parser.error("image limits must be positive")
    create_server(args.root, args.max_bytes, args.max_pixels).run(transport="stdio")


if __name__ == "__main__":
    main()
