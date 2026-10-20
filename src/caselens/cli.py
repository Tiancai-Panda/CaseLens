"""Installable CLI; machine-readable stdout and diagnostics on stderr."""

import asyncio
import io
import json
import logging
import sys
from pathlib import Path
from typing import Annotated

import typer
from PIL import Image, ImageDraw
from pydantic import ValidationError

from caselens.batch import BatchRunner
from caselens.cases import CaseStore, run_customer_case
from caselens.config import Settings
from caselens.errors import ClientError
from caselens.evaluation import evaluate as metrics
from caselens.replay import CUSTOMER_SCENARIOS
from caselens.report import markdown, save
from caselens.schemas import Policy
from caselens.service import Runtime, fingerprint, load_policy, run_case

app = typer.Typer(no_args_is_help=True, help="CaseLens：图片＋文字售后 Agent，补证恢复与政策内处理建议。")
DEFAULT_GOAL = "检查包装的可见破损与未知项，给出售后分诊建议"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")


@app.callback()
def configure(verbose: bool = False):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, stream=sys.stderr,
                        format="%(levelname)s %(name)s %(message)s", force=True)
    # SDK debug logging includes request bodies. Public CLI tracing stays metadata-only.
    logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def emit(value):
    # Windows PowerShell may decode native stdout with a legacy console code page.
    # ASCII JSON escapes preserve Unicode data regardless of that decoding choice.
    print(json.dumps(value, ensure_ascii=True, allow_nan=False))


def fail(code: str, message: str, exit_code: int):
    emit({"error": {"code": code, "message": message}})
    raise typer.Exit(exit_code)


def prepare(config, policy_file, state_dir, replay, strategy="inspect"):
    try:
        settings = Settings.load(config)
        if state_dir:
            settings.state_dir = state_dir
        policy = load_policy(policy_file)
        if replay:
            if replay not in CUSTOMER_SCENARIOS:
                raise ValueError("invalid replay scenario")
        elif strategy == "analyze":
            if not settings.vlm.base_url or not settings.vlm.model:
                raise ValueError("vlm.base_url and vlm.model must be configured")
            settings.vlm.key()
        else:
            settings.validate_live()
        return settings, policy
    except ValidationError:
        fail("configuration", "configuration or policy does not match its schema; use api_key for inline credentials", 2)
    except (ValueError, OSError):
        fail("configuration", "check config file, base_url/model, policy and api_key or environment credentials; run doctor", 2)


def display(result, format, output=None):
    if output:
        save(result, output)
    if format == "markdown":
        print(markdown(result))
    elif format == "user":
        print(result.user_message or result.summary)
        if result.case_id:
            print("工单编号：" + result.case_id)
    else:
        emit(result.model_dump(mode="json"))


def command(paths, goal, config, policy_file, state_dir, replay, order_id, strategy, format, output, user_text=""):
    if format not in {"json", "markdown", "user"}:
        fail("arguments", "format must be json, markdown or user", 2)
    settings, policy = prepare(config, policy_file, state_dir, replay, strategy)
    try:
        result = asyncio.run(run_case(paths, goal=goal, settings=settings, policy=policy,
                                      metadata={"order_id": order_id} if order_id else {}, replay=replay, strategy=strategy, user_text=user_text))
        display(result, format, output)
        if result.status in {"failed", "partial"}:
            raise typer.Exit(1)
    except ClientError as exc:
        fail(exc.code, exc.message, 1)
    except (ValueError, OSError):
        fail("invalid_input", "input must be a readable PNG/JPEG/WEBP within size limits; check output permissions", 1)


@app.command()
def analyze(
    image: Path, question: str = DEFAULT_GOAL,
    config: Path | None = None, state_dir: Path | None = None,
    replay: str | None = None, format: str = "json", output: Path | None = None,
):
    """提取图像证据；这是明确的单阶段调用，不是 Agent。"""
    command([image], question, config, None, state_dir, replay, None, "analyze", format, output)


@app.command()
def classify(
    image: Path, goal: str = DEFAULT_GOAL, order_id: str | None = None,
    config: Path | None = None, policy: Path | None = None, state_dir: Path | None = None,
    replay: str | None = None, format: str = "json", output: Path | None = None,
):
    """VLM → Jev 固定分类流程；不进行动态补证。"""
    command([image], goal, config, policy, state_dir, replay, order_id, "classify", format, output)


@app.command()
def inspect(
    images: Annotated[list[Path], typer.Argument(help="一至八张照片或截图")],
    goal: str = "核实售后诉求并给出处理建议", text: str = "", order_id: str | None = None,
    config: Path | None = None, policy: Path | None = None, state_dir: Path | None = None,
    replay: str | None = None, format: str = "json", output: Path | None = None,
):
    """图片＋--text 创建工单：识别诉求、核查、补证与处理建议。"""
    command(images, goal, config, policy, state_dir, replay, order_id, "inspect", format, output, user_text=text)


@app.command()
def resume(
    case_id: str, image: Annotated[list[Path] | None, typer.Option(help="补交图片，可重复使用 --image")] = None,
    text: str = "", order_id: str | None = None, replace_asset: str | None = None, replace_text: bool = False,
    retry: bool = False, recover: bool = False, config: Path | None = None, policy: Path | None = None,
    state_dir: Path | None = None, replay: str | None = None, format: str = "json", output: Path | None = None,
):
    """继续同一工单；--replace-asset 替换旧图；--retry 恢复失败运行。"""
    if format not in {"json", "markdown", "user"}:
        fail("arguments", "format must be json, markdown or user", 2)
    settings, rules = prepare(config, policy, state_dir, replay)
    try:
        result = asyncio.run(run_customer_case(image or [], user_text=text, goal="", metadata={"order_id": order_id} if order_id else {},
                                              settings=settings, policy=rules if policy else None, replay=replay, case_id=case_id,
                                              replace_asset_id=replace_asset, replace_text=replace_text, retry=retry, recover=recover))
        display(result, format, output)
        if result.status in {"failed", "partial"}:
            raise typer.Exit(1)
    except ClientError as exc:
        fail(exc.code, exc.message, 1)
    except (ValueError, OSError) as exc:
        # Application validation errors contain only controlled messages; filesystem exceptions may contain paths.
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, ValidationError) else "check case ID, images, policy and state directory"
        fail("case_input", message, 2)


@app.command("case")
def show_case(case_id: str, state_dir: Path | None = None, config: Path | None = None, history: bool = False):
    """查询已保存的工单、有效图片和最近结果；--history 显示历次运行。"""
    try:
        settings = Settings.load(config)
        with CaseStore(state_dir or settings.state_dir) as store:
            record = store.get(case_id)
            record.pop("claim", None)
            if history:
                record["history"] = store.history(case_id)
            emit(record)
    except (ValueError, OSError):
        fail("case_input", "case not found or configuration/state directory is invalid", 2)


@app.command()
def batch(
    directory: Path, goal: str = DEFAULT_GOAL, text: str = "", order_id: str | None = None,
    concurrency: Annotated[int, typer.Option(min=1, max=32)] = 4, resume: bool = False,
    config: Path | None = None, policy: Path | None = None, state_dir: Path | None = None,
    replay: str | None = None, output: Path | None = None,
):
    """每张图片一个任务；有界并发、JSONL 输出和内容/配置一致的恢复。"""
    settings, rules = prepare(config, policy, state_dir, replay)
    if not directory.is_dir():
        fail("invalid_input", "directory does not exist", 1)
    metadata = {"order_id": order_id} if order_id else {}
    paths = (path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
    handle = None
    try:
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            handle = output.open("w", encoding="utf-8")
        def on_result(row):
            line = json.dumps(row, ensure_ascii=False, allow_nan=False)
            emit(row)
            if handle:
                handle.write(line + "\n")
                handle.flush()
        async def work():
            async def execute(runtime=None):
                async def process(path):
                    return await run_case([path], goal=goal, user_text=text, settings=settings, policy=rules, metadata=metadata, replay=replay, runtime=runtime)
                key = fingerprint(settings, rules, goal, {**metadata, "user_text": text}, f"replay:{replay}" if replay else "live")
                with BatchRunner(settings.state_dir / "batch.sqlite3", key, concurrency) as runner:
                    return await runner.run(paths, process, on_result, resume=resume)
            if replay:
                return await execute()
            async with Runtime(settings) as runtime:
                return await execute(runtime)
        summary = asyncio.run(work())
        print(json.dumps({"batch_summary": summary}, ensure_ascii=False), file=sys.stderr)
        if summary["failed"] or summary["partial"]:
            raise typer.Exit(3)
    except ClientError as exc:
        fail(exc.code, exc.message, 1)
    except (ValueError, OSError):
        fail("invalid_input", "batch input/output or configuration is invalid", 1)
    finally:
        if handle:
            handle.close()


@app.command()
def chat(config: Path | None = None, policy: Path | None = None, state_dir: Path | None = None, replay: str | None = None):
    """面向用户的终端售后对话；补证继续同一工单，/new 开始新工单。"""
    settings, rules = prepare(config, policy, state_dir, replay)
    current_case = None
    while True:
        try:
            print("图片路径（首轮空行退出；补证可空；/new 新工单；/quit 退出）:", file=sys.stderr)
            line = input().strip().strip('"')
            if line == "/quit" or (not line and current_case is None):
                return
            if line == "/new":
                current_case = None
                continue
            print("问题描述或补充说明:", file=sys.stderr)
            text = input().strip()
            print("订单编号（可空）:", file=sys.stderr)
            order = input().strip()
            if not line and not text and not order:
                return
            result = asyncio.run(run_customer_case([Path(line)] if line else [], user_text=text, goal="核实售后诉求并给出处理建议", settings=settings,
                                                   policy=rules if current_case is None else None, metadata={"order_id": order} if order else {}, replay=replay, case_id=current_case))
            emit(result.model_dump(mode="json"))
            print(result.user_message, file=sys.stderr)
            current_case = result.case_id
        except (EOFError, KeyboardInterrupt):
            return
        except ClientError as exc:
            emit({"error": {"code": exc.code, "message": exc.message}})
        except (ValueError, OSError):
            emit({"error": {"code": "invalid_input", "message": "check the input image and configuration"}})


@app.command()
def doctor(config: Path | None = None, probe: bool = False):
    """检查配置及密钥是否存在，不显示密钥；--probe 启用 API 冒烟调用。"""
    try:
        settings = Settings.load(config)
    except (ValueError, OSError):
        fail("configuration", "configuration file is missing or invalid; put inline credentials in api_key", 2)
    status = {}
    for name, endpoint in [("vlm", settings.vlm), ("controller", settings.controller or settings.vlm), ("jev", settings.jev)]:
        try:
            endpoint.key()
            key_present = True
        except ValueError:
            key_present = False
        status[name] = {"model": endpoint.model, "key_env": endpoint.api_key_env, "key_present": key_present,
                        "key_source": endpoint.key_source(),
                        "endpoint_configured": bool(endpoint.base_url)}
    if probe:
        stage = "configuration"
        try:
            settings.validate_live()
            async def smoke():
                nonlocal stage
                async with Runtime(settings) as runtime:
                    buffer = io.BytesIO()
                    Image.new("RGB", (32, 32), "white").save(buffer, format="PNG")
                    stage = "vlm"
                    observation = await runtime.vlm.analyze(buffer.getvalue(), "image/png", question="Describe only the visible synthetic image.")
                    stage = "jev_intent"
                    intent = await runtime.jev.detect_intent({"user_text": "包装破损"})
                    if not intent.issues:
                        raise ClientError("invalid_response", "intent smoke test did not identify a complaint")
                    stage = "jev_assessment"
                    state = {"evidence": [observation.model_dump()], "user_text": "包装破损", "metadata": {}}
                    findings = await runtime.jev.assess_issues(state, issues=intent.issues, policy=Policy(), evidence_ids=["smoke"])
                    stage = "jev_recommendation"
                    recommendation = await runtime.jev.recommend(state, issue=intent.issues[0], policy=Policy(), allowed_actions=["human_review"], evidence_ids=["smoke"])
                    stage = "controller"
                    action = await runtime.controller.next_action([
                        {"role": "system", "content": "Call read_policy with policy_id default."},
                        {"role": "user", "content": "Check tool calling."},
                    ], tool_schemas=[{"type": "function", "function": {"name": "read_policy", "description": "Read a policy", "parameters": {"type": "object", "properties": {"policy_id": {"type": "string"}}, "required": ["policy_id"], "additionalProperties": False}}}])
                    return {"vlm_schema": True, "jev_schema": bool(findings), "jev_intent": bool(intent.issues), "jev_recommendation": recommendation.action.choice == "human_review", "native_tool_call": action.calls[0].name == "read_policy"}
            status["probe"] = asyncio.run(smoke())
        except ClientError as exc:
            emit({"error": {"code": exc.code, "message": exc.message, "stage": stage}})
            raise typer.Exit(1)
        except ValueError:
            fail("configuration", "configure base_url/model and api_key or environment credentials before probing", 2)
    emit(status)


@app.command()
def demo(scenario: str = "clear", output_dir: Path = Path("outputs/demo")):
    """离线售后流程回放：clear/blurred/missing/irrelevant/quantity/multiple。"""
    if scenario not in CUSTOMER_SCENARIOS:
        fail("arguments", "unknown replay scenario", 2)
    directory = output_dir / scenario
    try:
        directory.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (480, 320), "#eee9dc")
        draw = ImageDraw.Draw(image)
        draw.rectangle((50, 40, 430, 280), fill="#bd8b53", outline="#59452a", width=4)
        draw.line((360, 230, 385, 248, 365, 265, 420, 280), fill="#faf6ec", width=7)
        draw.text((70, 60), "SYNTHETIC REPLAY FIXTURE", fill="#000000")
        source = directory / "synthetic.png"
        image.save(source)
        settings = Settings(state_dir=directory / "state")
        result = asyncio.run(run_case([source], goal=DEFAULT_GOAL, user_text="包装破损，收到的数量也可能不够", settings=settings, policy=Policy(),
                                      metadata={} if scenario == "missing" else {"order_id": "SYNTHETIC-DEMO"}, replay=scenario))
        save(result, directory)
        emit(result.model_dump(mode="json"))
        if result.status in {"failed", "partial"}:
            raise typer.Exit(1)
    except (ValueError, OSError):
        fail("invalid_input", "could not write demo artifacts", 1)


@app.command("evaluate")
def evaluate_command(rows: Path, labels: str = "packaging_issue,product_issue,insufficient_information", output: Path | None = None):
    """汇总已标注 JSONL；支持四类基线分别评测，不制造模型成绩。"""
    try:
        records = [json.loads(line) for line in rows.read_text(encoding="utf-8").splitlines() if line.strip()]
        if any(not isinstance(row, dict) for row in records):
            raise ValueError("each row must be an object")
        modes = {row.get("mode", "unspecified") for row in records}
        if len(modes) > 1:
            raise ValueError("do not mix replay and live results")
        result = {"mode": next(iter(modes), "unspecified"), "metrics": metrics(records, labels=labels.split(","))}
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        emit(result)
    except (ValueError, OSError, TypeError):
        fail("invalid_dataset", "check distinct case_ids, labels, probabilities and consistent run mode", 2)


@app.command("skills")
def skills_command(name: str | None = None):
    """列出内置 Skill，或用 --name 查看说明和资源；不调用模型。"""
    from caselens.skills import SkillRegistry
    try:
        registry = SkillRegistry()
        emit(registry.activate(name) if name else {"skills": registry.catalog()})
    except (ValueError, OSError):
        fail("skill_input", "unknown skill or invalid bundled resources", 2)


@app.command("mcp-server")
def mcp_server_command(root: Path):
    """启动受限图片 MCP stdio 服务，stdout 仅输出协议。"""
    from caselens.image_server import create_server
    create_server(root).run(transport="stdio")


if __name__ == "__main__":
    app()
