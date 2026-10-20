"""LangGraph orchestration with bounded native tool use and case checkpoints."""

import asyncio
import inspect
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from caselens.context import ContextBudgetError, ContextManager
from caselens.errors import ClientError
from caselens.image_mcp import ImageMCPClient
from caselens.prompts import CONTROL
from caselens.schemas import RunResult, StepRecord, ToolCall, ToolResult
from caselens.tools import ToolService
from caselens.tracing import CURRENT_CALLS, CURRENT_RUN


class GraphState(TypedDict):
    messages: list[dict]
    round_number: int
    calls: list[dict]
    fatal: bool
    halted: bool
    groups: list[list[dict]]


class Agent:
    def __init__(self, service: ToolService, controller, *, mode: str, event_sink=None):
        self.service, self.controller, self.mode, self.event_sink = service, controller, mode, event_sink
        self.result = None
        self.started = 0.0
        builder = StateGraph(GraphState)
        for name in ("bootstrap", "load_skills", "prepare_context", "controller", "tools", "checkpoint", "finalize"):
            builder.add_node(name, getattr(self, "_" + name))
        builder.add_edge(START, "bootstrap")
        builder.add_conditional_edges("bootstrap", lambda state: "finalize" if self._stopped(state) else "load_skills")
        builder.add_edge("load_skills", "prepare_context")
        builder.add_conditional_edges("prepare_context", lambda state: "finalize" if self._stopped(state) else "controller")
        builder.add_conditional_edges("controller", lambda state: "finalize" if self._stopped(state) else "tools" if state["calls"] else "checkpoint")
        builder.add_edge("tools", "checkpoint")
        builder.add_conditional_edges("checkpoint", lambda state: "finalize" if self._stopped(state) or state["round_number"] >= self.service.settings.max_rounds else "prepare_context")
        builder.add_edge("finalize", END)
        self.graph = builder.compile()

    def _stopped(self, state):
        return bool(self.service.final_status or state["fatal"] or state["halted"])

    async def _event(self, payload):
        if self.event_sink is not None:
            emitted = self.event_sink(payload)
            if inspect.isawaitable(emitted):
                await emitted

    async def _node(self, name):
        await self._event({"type": "graph_node", "node": name, "runtime": "langgraph"})

    async def run(self, goal: str) -> RunResult:
        self.result = RunResult(mode=self.mode, status="failed", goal=goal, runtime="langgraph")
        self.started = time.monotonic()
        token = CURRENT_RUN.set(self.result.run_id)
        calls_token = CURRENT_CALLS.set([])
        try:
            await self._event({"type": "run_started", "run_id": self.result.run_id, "mode": self.mode})
            initial = GraphState(messages=[], groups=[], round_number=0, calls=[], fatal=False, halted=False)
            image_context = ImageMCPClient(self.service.assets)
            image_client = None
            try:
                image_client = await image_context.__aenter__()
            except (RuntimeError, OSError, ValueError) as exc:
                self.result.errors.append({
                    "code": "mcp_unavailable",
                    "message": "image MCP service unavailable",
                    "stage": "mcp",
                    "detail": str(exc),
                })
                self.result.summary = "图片处理工具不可用，已停止运行；请检查 MCP 子进程和图片目录。"
                return self.result
            try:
                self.service.image_client = image_client
                await self._event({"type": "mcp_connected", "tools": [s["function"]["name"] for s in image_client.schemas()]})
                await self.graph.ainvoke(initial, {"recursion_limit": self.service.settings.max_rounds * 5 + 10})
            finally:
                await image_context.__aexit__(None, None, None)
            return self.result
        finally:
            self.service.image_client = None
            CURRENT_CALLS.reset(calls_token)
            CURRENT_RUN.reset(token)

    async def _bootstrap(self, state):
        await self._node("bootstrap")
        if hasattr(self.service, "bootstrap"):
            try:
                await self._event({"type": "intent_started"})
                await self.service.bootstrap()
                await self._event({"type": "intent_finished", "issues": self.service.intent.issues if self.service.intent else []})
                if hasattr(self.service, "checkpoint"):
                    self.service.checkpoint()
            except ClientError as exc:
                self.result.errors.append({"code": exc.code, "message": exc.message, "stage": "intent"})
                return {"fatal": True}
        return {}

    async def _load_skills(self, state):
        await self._node("load_skills")
        issues = self.service.intent.issues if getattr(self.service, "intent", None) else []
        for skill in self.service.skill_registry.select(issues):
            await self.service.activate_skill(skill["name"])
        await self._event({"type": "skills_loaded", "skills": [s["name"] for s in self.service.active_skills]})
        return {}

    async def _prepare_context(self, state):
        await self._node("prepare_context")
        service = self.service
        manager = ContextManager(max_input_tokens=service.settings.context_max_input_tokens,
                                 output_reserve=service.settings.context_output_reserve,
                                 recent_groups=service.settings.context_recent_groups)
        current = {**service.state(), "remaining_rounds": service.settings.max_rounds - state["round_number"],
                   "skill_catalog": service.skill_registry.catalog(), "quantity_check": service.quantity_check,
                   "controller_errors": [error["code"] for error in self.result.errors if error.get("stage") == "controller"][-8:],
                   "max_supplements": service.settings.max_supplements, "threshold": service.settings.threshold,
                   "policy_id": service.policy.policy_id, "policy_version": service.policy.version}
        try:
            messages, stats, memory = manager.prepare(system=getattr(service, "control_prompt", CONTROL),
                goal=self.result.goal, state=current, schemas=service.registry().schemas(), groups=state["groups"],
                skills=[*service.active_skills, *[{"name": r["name"] + "/" + r["path"], "version": "resource", "content": r["content"]} for r in service.skill_resources]], prior_memory=getattr(service, "prior_memory", {}))
        except (ContextBudgetError, ValueError) as exc:
            self.result.errors.append({"code": getattr(exc, "code", "invalid_context"), "message": str(exc), "stage": "context"})
            return {"halted": True}
        service.memory = memory
        self.result.context_stats = stats
        await self._event({"type": "context_prepared", **stats})
        return {"messages": messages}

    async def _controller(self, state):
        await self._node("controller")
        round_number = state["round_number"] + 1
        messages = list(state["messages"])
        try:
            await self._event({"type": "round_started", "round": round_number})
            action = await self.controller.next_action(messages, tool_schemas=self.service.registry().schemas())
        except ClientError as exc:
            self.result.errors.append({"code": exc.code, "message": exc.message, "retryable": exc.retryable, "stage": "controller"})
            # The Client has already exhausted transport retries. Only malformed calls get a repair round.
            fatal = exc.code != "invalid_tool_calls"
            messages.append({"role": "user", "content": f"Controller error: {exc.code}. Return valid native tool calls or stop safely."})
            return {"round_number": round_number, "fatal": fatal, "messages": messages, "calls": []}
        if not action.calls:
            self.result.errors.append({"code": "invalid_tool_calls", "message": "no tools selected"})
            return {"round_number": round_number, "calls": []}
        if len(action.calls) > self.service.settings.max_calls_per_round:
            self.result.errors.append({"code": "too_many_calls", "message": "controller call budget exceeded"})
            return {"round_number": round_number, "calls": [], "halted": True}
        messages.append(action.message)
        return {"round_number": round_number, "calls": [call.model_dump() for call in action.calls], "messages": messages}

    async def _tools(self, state):
        await self._node("tools")
        calls = [ToolCall.model_validate(call) for call in state["calls"]]
        for call in calls:
            await self._event({"type": "tool_started", "round": state["round_number"], "tool": call.name})
        independent = {"read_policy", "inspect_asset", "analyze_visual"}
        if len(calls) > 1 and not {call.name for call in calls}.issubset(independent):
            outputs = [ToolResult(ok=False, error_code="dependent_calls", message="dependent or terminal tools must be selected in separate rounds") for _ in calls]
        else:
            outputs = await asyncio.gather(*(self.service.registry().execute(call) for call in calls))
        messages = list(state["messages"])
        group = [messages[-1]]
        for call, output in zip(calls, outputs):
            self.result.steps.append(StepRecord(round=state["round_number"], call=call, result=output))
            await self._event({"type": "tool_finished", "round": state["round_number"], "tool": call.name, "ok": output.ok, "error_code": output.error_code})
            tool_message = {"role": "tool", "tool_call_id": call.call_id, "content": output.model_dump_json()}
            messages.append(tool_message)
            group.append(tool_message)
            if not output.ok:
                self.result.errors.append({"code": output.error_code, "message": output.message, "call_id": call.call_id})
        return {"messages": messages, "calls": [], "groups": [*state["groups"], group]}

    async def _checkpoint(self, state):
        await self._node("checkpoint")
        self.service.memory = ContextManager.summarize(state["groups"], self.service.state())
        prior = getattr(self.service, "prior_memory", {})
        self.service.memory["prior_actions"] = [*prior.get("prior_actions", []), *prior.get("actions", [])][-32:]
        self.service.memory["prior_errors"] = list(dict.fromkeys([*prior.get("prior_errors", []), *prior.get("errors", [])]))[-16:]
        self.service.memory["controller_errors"] = [error["code"] for error in self.result.errors if error.get("stage") == "controller"][-8:]
        self.service.memory["policy_version"] = self.service.policy.version
        if hasattr(self.service, "checkpoint"):
            self.service.checkpoint()
        return {}

    async def _finalize(self, state):
        await self._node("finalize")
        service, result = self.service, self.result
        if not service.final_status and not state["fatal"] and hasattr(service, "on_budget"):
            await service.on_budget()
            result.errors.append({"code": "budget_or_execution_stopped", "message": "bounded execution stopped; guidance saved"})
        if service.final_status:
            result.status = service.final_status
        elif state["fatal"]:
            result.status = "failed"
        else:
            result.status = "needs_review" if service.decision else ("partial" if service.evidence else "failed")
            result.errors.append({"code": "budget_or_execution_stopped", "message": "agent stopped without an accepted finalization"})
        result.evidence, result.decision = list(service.evidence.values()), service.decision
        result.summary = service.summary or "自动处理未完成；请复核现有证据与执行记录。"
        result.requested_information = service.requested_information
        result.calls = list(CURRENT_CALLS.get() or [])
        result.latency_ms = (time.monotonic() - self.started) * 1000
        if hasattr(service, "enrich_result"):
            service.enrich_result(result)
        result.memory = getattr(service, "memory", {})
        result.skills = [{"name": skill["name"], "version": skill.get("version")} for skill in getattr(service, "active_skills", [])]
        await self._event({"type": "run_finished", "status": result.status, "requested_information": result.requested_information})
        return {}
