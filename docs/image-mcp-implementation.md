# Image MCP implementation — 2026-10-12

Implemented Task 2 in the WSL project only. Owned files: image_mcp.py, image_server.py, image_service.py and tests/test_image_mcp.py. Shared AssetRef fields were supplied by the root agent.

## Current integration status (2026-10-12)

ImageMCPClient is connected to Agent.run and ToolService; Controller discovers crop/resize/rotate schemas. CLI/Web Agent runs automatically start and close the stdio process. CLI mcp-server --root serves external Hosts, which first register a relative image path. MCP startup failure returns a typed mcp_unavailable result.

Full-project offline verification passed 203 tests in addition to the 18-test owned MCP suite. Web can serve persistent derived images by case/asset ID. Model APIs were not called. See [Chinese guide](../PROJECT_GUIDE.zh-CN.md) for commands and boundaries.

## Integration interface

```python
async with ImageMCPClient(assets, timeout=15.0) as images:
    declarations = images.schemas()
    asset = await images.call("crop_image", {"asset_id": source_id, "region": [0, 0, 1, 1]})
```

schemas() returns OpenAI function declarations discovered through MCP list_tools for crop_image, resize_image, rotate_image. call() returns AssetRef, inserts it into assets.assets, and caches verified bytes in assets._crops. Resize parameters are width/height positive pixel integers; rotate parameter is angle in [-360, 360]. Controller/service integration is implemented through ToolService and the LangGraph Agent. Context entry and exit must happen within the same async task because the SDK uses AnyIO task-group scopes.

Independent server command:

```sh
.venv/bin/python -m caselens.image_server --root /path/to/allowed/image/root
```

External Hosts first call register_image(relative_path), then use returned asset IDs. register_assets is a controlled client synchronization tool: all source/derived paths are validated under the configured root, original registry metadata must match independently registered source metadata, hashes/dimensions are checked, and legacy in-memory crop bytes can be imported.

## Persistence and provenance

Source manifests and derived PNGs live under assets.root/.image-tools. Artifact writes use temporary files plus replacement, and original images are not overwritten. Manifest reload restores derived assets across server restarts. source_path stays the original image; derived_path points to persistent pixels. Root/parent IDs and operation parameters are recorded in transforms. Crop coordinates use actual rounded pixel bounds and compose safe source regions. Rotation clears region and subsequent crops retain region=None because their coordinates cannot be reported as unrotated original regions.

Service validates image formats, byte/pixel limits, finite normalized crop regions, positive crop area and one-pixel minimum, resize dimensions, finite bounded angles, root confinement, symlink escape and source hashes before transformation. Inputs changed after registration produce explicit errors.

## Protocol and lifecycle

The client actually spawns sys.executable -m caselens.image_server and executes initialize, list_tools, register_assets, call_tool through the pinned MCP SDK 1.30.0 stdio transport. There is no local fallback. Typed output models are needed in this SDK: bare dict annotations otherwise produce text-only results. Unstructured successful results are rejected. Failed tools raise ValueError; transport/timeout failures raise RuntimeError; task cancellation is propagated.

Client environment explicitly permits OS/path/locale/temp variables plus a trusted package PYTHONPATH, and excludes provider keys and untrusted inherited Python search paths. SDK adds its own small safe OS default environment. The service does not import application configuration or model clients. Logs stay on stderr; stdout belongs to the protocol.

The SDK can emit a BrokenResourceError in its stdout reader during cleanup after a frozen server resumes and delivers a timed-out response. Cleanup only suppresses BrokenResourceError/ClosedResourceError; other exceptions remain visible. The SDK owns close/terminate/kill of subprocesses.

## Verification

- Initial red phase: 13 tests failed with assertions that the client/service feature was missing.
- Initial green phase: 13 passed, Ruff clean.
- Additional lifecycle/confined-manifest checks exposed timeout cleanup failure and a manifest symlink escape; both were corrected after failing tests.
- Final owned suite: 18 passed in 7.27 seconds; Ruff on owned modules/tests: all checks passed.
- Real stdio subprocess exercised discovery, crop/resize/rotate, unknown IDs, invalid geometry, source changes, original preservation, chained provenance, rotated regions, restart recovery, old local crop import, relative registration/path escape, environment secret exclusion, killed server, frozen-server timeout and task cancellation/process cleanup.
- No live VLM, Jev or controller model calls.

Lifecycle tests inspect /proc and signal actual child processes, so these five checks are Linux/WSL focused. Full-project tests, graph integration and AssetStore derived-path recovery are included in the integrated verification. Atomic files prevent partial artifacts; this implementation does not provide a cross-process registry lock for independent concurrent Hosts sharing one root.
