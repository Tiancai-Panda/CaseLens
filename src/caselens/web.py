"""Local FastAPI workbench for the customer-facing CaseLens Agent."""

import asyncio
import base64
import binascii
import json
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import Future
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from caselens.assets import AssetStore
from caselens.cases import CaseStore, run_customer_case
from caselens.config import Settings
from caselens.errors import ClientError
from caselens.replay import CUSTOMER_SCENARIOS
from caselens.report import markdown
from caselens.schemas import Policy, RunResult

MAX_UPLOADS = 8
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


class ImagePayload(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_type: str
    data_base64: str = Field(min_length=1, max_length=28 * 1024 * 1024)


class JobRequest(BaseModel):
    text: str = Field(default="", max_length=8000)
    order_id: str = Field(default="", max_length=200)
    images: list[ImagePayload] = Field(default_factory=list, max_length=MAX_UPLOADS)
    replay: str | None = None


class ResumeRequest(JobRequest):
    replace_asset_id: str | None = None
    replace_text: bool = False
    retry: bool = False
    recover: bool = False


class WebJobManager:
    def __init__(self, *, state_dir: Path, config_path: Path | None = None):
        self.state_dir = Path(state_dir)
        self.config_path = config_path
        self.jobs: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, Future] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.done: dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self.loop = asyncio.new_event_loop()
        self.worker = None
        self.runtime = None
        self.runtime_lock = None
        self.closing = False

    def _ensure_worker(self):
        if self.worker is None:
            self.worker = threading.Thread(target=self.loop.run_forever, daemon=True, name="caselens-web")
            self.worker.start()

    async def _live_runtime(self):
        from caselens.service import Runtime
        if self.runtime_lock is None:
            self.runtime_lock = asyncio.Lock()
        async with self.runtime_lock:
            if self.runtime is None:
                runtime = Runtime(self._settings())
                await runtime.__aenter__()
                self.runtime = runtime
            return self.runtime

    async def _shutdown(self):
        tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.runtime:
            await self.runtime.__aexit__(None, None, None)

    def close(self):
        self.closing = True
        if self.worker:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop).result(timeout=10)
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.worker.join(timeout=10)
        self.loop.close()

    def _append(self, job_id: str, event: dict[str, Any]):
        with self.lock:
            self.events.setdefault(job_id, []).append({"seq": len(self.events.get(job_id, [])) + 1, "time": time.time(), **event})

    def _decode(self, job_id: str, images: list[ImagePayload]):
        if len(images) > MAX_UPLOADS:
            raise ValueError("at most eight images")
        directory = (self.state_dir / "web-inbox" / job_id).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for index, image in enumerate(images):
            if image.content_type not in ALLOWED_TYPES:
                raise ValueError("only PNG, JPEG and WEBP uploads are supported")
            try:
                data = base64.b64decode(image.data_base64, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("image data is not valid base64") from exc
            if not data or len(data) > MAX_UPLOAD_BYTES:
                raise ValueError("image exceeds the 20 MiB limit")
            suffix = ALLOWED_TYPES[image.content_type]
            target = directory / f"upload-{index}{suffix}"
            target.write_bytes(data)
            AssetStore(directory).register(target)
            paths.append(target)
        return paths

    def _settings(self):
        settings = Settings.load(self.config_path)
        settings.state_dir = self.state_dir
        return settings

    def submit(self, request: JobRequest, *, case_id=None, resume=False):
        if not request.text.strip() and not request.images and not resume:
            raise ValueError("provide text or at least one image")
        if request.replay and request.replay not in CUSTOMER_SCENARIOS:
            raise ValueError("invalid replay scenario")
        job_id = "job_" + uuid.uuid4().hex
        record = {"job_id": job_id, "case_id": case_id, "status": "queued", "result": None, "error": None}
        with self.lock:
            if self.closing:
                raise HTTPException(503, "服务器正在关闭")
            if sum(job["status"] in {"queued", "running"} for job in self.jobs.values()) >= 2:
                raise HTTPException(status_code=429, detail="已有两个任务运行，请等待结束。")
            if case_id and any(job["case_id"] == case_id and job["status"] in {"queued", "running"} for job in self.jobs.values()):
                raise HTTPException(status_code=409, detail="当前工单仍在运行。")
            for old_id in list(self.jobs):
                if len(self.jobs) < 50:
                    break
                if self.done.get(old_id) and self.done[old_id].is_set():
                    self.jobs.pop(old_id, None)
                    self.events.pop(old_id, None)
                    self.tasks.pop(old_id, None)
                    self.done.pop(old_id, None)
            self.jobs[job_id] = record
            self.events[job_id] = []
            self.done[job_id] = threading.Event()
        try:
            paths = self._decode(job_id, request.images)
        except (ValueError, OSError):
            shutil.rmtree(self.state_dir / "web-inbox" / job_id, ignore_errors=True)
            with self.lock:
                self.jobs.pop(job_id, None)
                self.events.pop(job_id, None)
                self.done.pop(job_id, None)
            raise ValueError("图片无效，请上传不超过 20 MiB 的 PNG/JPEG/WEBP") from None
        self._append(job_id, {"type": "queued"})
        with self.lock:
            self._ensure_worker()
            self.tasks[job_id] = asyncio.run_coroutine_threadsafe(self._run(job_id, request, paths, case_id=case_id, resume=resume), self.loop)
        return dict(record)

    async def _run(self, job_id, request, paths, *, case_id, resume):
        self.jobs[job_id]["status"] = "running"
        self._append(job_id, {"type": "job_started"})

        def event_sink(event):
            if event.get("case_id"):
                self.jobs[job_id]["case_id"] = event["case_id"]
            self._append(job_id, event)

        try:
            runtime = None if request.replay else await self._live_runtime()
            settings = Settings(state_dir=self.state_dir) if request.replay else runtime.settings
            result = await run_customer_case(
                paths,
                user_text=request.text,
                goal="核实售后诉求并给出处理建议",
                metadata={"order_id": request.order_id} if request.order_id else {},
                settings=settings,
                policy=None if resume else Policy(),
                replay=request.replay,
                case_id=case_id,
                replace_asset_id=getattr(request, "replace_asset_id", None),
                replace_text=getattr(request, "replace_text", False),
                retry=getattr(request, "retry", False),
                recover=getattr(request, "recover", False),
                runtime=runtime,
                event_sink=event_sink,
            )
            self.jobs[job_id].update(status=result.status, case_id=result.case_id, result=result.model_dump(mode="json"))
            self._append(job_id, {"type": "job_finished", "status": result.status, "case_id": result.case_id})
        except asyncio.CancelledError:
            self.jobs[job_id].update(status="partial", error={"code": "cancelled", "message": "任务已中断，已获取材料保存在工单中。"})
            self._append(job_id, {"type": "job_failed", "message": "任务已中断"})
        except Exception as exc:
            if isinstance(exc, ClientError):
                error = {"code": exc.code, "message": exc.message}
            elif isinstance(exc, ValidationError):
                error = {"code": "configuration", "message": "服务器配置格式错误，请检查本地 caselens.json。"}
            else:
                error = {"code": "web_job_failed", "message": "任务未能运行，请检查配置、工单状态及提交材料。"}
            self.jobs[job_id].update(status="failed", error=error)
            self._append(job_id, {"type": "job_failed", **error})
        finally:
            self.done[job_id].set()
            shutil.rmtree(self.state_dir / "web-inbox" / job_id, ignore_errors=True)

def create_app(*, state_dir: Path | str | None = None, config_path: Path | str | None = None):
    config = Path(config_path or os.environ.get("CASELENS_WEB_CONFIG", "caselens.json"))
    if state_dir is None:
        try:
            state_dir = Settings.load(config).state_dir
        except (ValueError, OSError):
            state_dir = Path(".caselens")
    manager = WebJobManager(state_dir=Path(state_dir).resolve(), config_path=config)
    @asynccontextmanager
    async def lifespan(app):
        yield
        await asyncio.to_thread(manager.close)
    app = FastAPI(title="CaseLens Workbench", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "仅允许同源本地请求。"}, status_code=403)
        if request.method == "POST" and not request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse({"detail": "需要 application/json"}, status_code=415)
        try:
            length = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            return JSONResponse({"detail": "无效请求长度"}, status_code=400)
        if length > 225 * 1024 * 1024:
            return JSONResponse({"detail": "请求体超出大小限制"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response
    app.state.web_jobs = manager
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    async def health():
        try:
            settings = manager._settings()
            settings.validate_live()
            ready = True
        except (ValueError, OSError):
            ready = False
        return {"status": "ok", "service": "caselens-web", "credentials": "server-side", "live_ready": ready}

    @app.post("/api/jobs", status_code=202)
    async def create_job(request: JobRequest):
        try:
            return await asyncio.to_thread(manager.submit, request)
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/skills")
    async def skill_catalog():
        from caselens.skills import SkillRegistry
        return {"skills": SkillRegistry().catalog()}

    @app.get("/api/jobs/{job_id}")
    async def job(job_id: str):
        if job_id not in manager.jobs:
            raise HTTPException(status_code=404, detail="job not found in this process")
        with manager.lock:
            return dict(manager.jobs[job_id])

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, after: int = 0):
        if job_id not in manager.jobs:
            raise HTTPException(status_code=404, detail="job not found in this process")
        with manager.lock:
            return {"events": [event for event in manager.events[job_id] if event["seq"] > after]}

    @app.get("/api/jobs/{job_id}/stream")
    async def event_stream(job_id: str):
        if job_id not in manager.jobs:
            raise HTTPException(404, "job not found")
        async def stream():
            last_seq = 0
            while job_id in manager.jobs:
                with manager.lock:
                    events = [dict(event) for event in manager.events[job_id] if event["seq"] > last_seq]
                    finished = manager.done[job_id].is_set()
                for event in events:
                    last_seq = event["seq"]
                    yield "data: " + json.dumps(event, ensure_ascii=True) + "\n\n"
                if finished:
                    break
                yield ": heartbeat\n\n"
                await asyncio.sleep(.5)
        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/cases")
    async def list_cases():
        with CaseStore(manager.state_dir) as store:
            records = [json.loads(row[0]) for row in store.connection.execute("SELECT payload FROM cases ORDER BY rowid DESC LIMIT 50")]
        return {"cases": [{"case_id": r["case_id"], "status": r["status"], "mode": r["mode"], "submission": r["submission"], "text": r["messages"][-1]["text"], "order_id": r["metadata"].get("order_id", "")} for r in records]}

    @app.get("/api/cases/{case_id}/images/{asset_id}")
    async def image(case_id: str, asset_id: str):
        try:
            with CaseStore(manager.state_dir) as store:
                record = store.get(case_id)
                if not any(image["asset_id"] == asset_id for image in record["images"]):
                    assets, _, _ = store.restore(record)
                    derived = assets.get(asset_id)
                    data = assets.data(asset_id)
                    return Response(data, media_type=derived.mime_type)
                asset = next(image for image in record["images"] if image["asset_id"] == asset_id)
                path = Path(asset["managed_path"]).resolve()
                if not path.is_relative_to(store.directory(case_id)):
                    raise ValueError("invalid source")
                AssetStore(store.directory(case_id)).register(path)
                return FileResponse(path)
        except (ValueError, OSError, StopIteration) as exc:
            raise HTTPException(404, "image unavailable") from exc

    @app.get("/api/cases/{case_id}/report")
    async def report(case_id: str):
        try:
            with CaseStore(manager.state_dir) as store:
                record = store.get(case_id)
            result = RunResult.model_validate(record["last_result"])
            return Response(markdown(result), media_type="text/markdown", headers={"Content-Disposition": 'attachment; filename="report.md"'})
        except (ValueError, OSError) as exc:
            raise HTTPException(404, "report unavailable") from exc

    @app.get("/api/cases/{case_id}")
    async def case(case_id: str, history: bool = False):
        try:
            with CaseStore(manager.state_dir) as store:
                record = store.get(case_id)
                record.pop("claim", None)
                if history:
                    record["history"] = store.history(case_id)
                return record
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="case not found") from exc

    @app.post("/api/cases/{case_id}/resume", status_code=202)
    async def resume(case_id: str, request: ResumeRequest):
        try:
            with CaseStore(manager.state_dir) as store:
                record = store.get(case_id)
            if record["mode"] == "replay" and not request.replay:
                raise ValueError("replay cases require a replay scenario")
            if record["mode"] == "live" and request.replay:
                raise ValueError("live cases cannot switch to replay")
            return await asyncio.to_thread(manager.submit, request, case_id=case_id, resume=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


app = create_app()
