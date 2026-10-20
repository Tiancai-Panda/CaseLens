"""Explicit recorded scenarios for offline workflow demonstrations, never model inference."""

import json

from caselens.schemas import (
    AgentAction,
    CallRecord,
    ChoicePrediction,
    DecisionResult,
    ToolCall,
    VisualObservation,
    new_id,
)
from caselens.tracing import CURRENT_RUN, record_call

SCENARIOS = {
    "clear": {
        "observations": [{"facts": ["纸箱右下角有可见撕裂"], "text": [], "unknowns": ["包装内商品状态不可见"]}],
        "decisions": [("packaging_issue", "sufficient")],
        "plan": [
            [{"name": "read_policy", "arguments": {"policy_id": "default"}}, {"name": "analyze_visual", "arguments": {"asset_id": "$asset", "question": "检查包装的可见破损与未知项"}}],
            [{"name": "evaluate_decision", "arguments": {"evidence_ids": "$evidence"}}],
            [{"name": "finish", "arguments": {"summary": "完成分诊"}}],
        ],
    },
    "blurred": {
        "observations": [
            {"facts": ["纸箱右下角存在模糊区域"], "text": [], "unknowns": ["无法确认该区域是撕裂还是阴影"]},
            {"facts": ["局部放大后可见纸箱边缘撕裂"], "text": [], "unknowns": ["包装内商品状态不可见"]},
        ],
        "decisions": [("insufficient_information", "needs_visual_review"), ("packaging_issue", "sufficient")],
        "plan": [
            [{"name": "read_policy", "arguments": {"policy_id": "default"}}, {"name": "analyze_visual", "arguments": {"asset_id": "$asset", "question": "检查包装的可见破损与未知项"}}],
            [{"name": "evaluate_decision", "arguments": {"evidence_ids": "$evidence"}}],
            [{"name": "crop_image", "arguments": {"asset_id": "$asset", "region": [.5, .5, 1., 1.]}}],
            [{"name": "analyze_visual", "arguments": {"asset_id": "$crop", "question": "检查右下角是否有可见撕裂"}}],
            [{"name": "evaluate_decision", "arguments": {"evidence_ids": "$evidence"}}],
            [{"name": "finish", "arguments": {"summary": "补证完成"}}],
        ],
    },
    "missing": {
        "observations": [{"facts": ["可见外包装"], "text": [], "unknowns": ["缺少订单标识及商品照片"]}],
        "decisions": [("insufficient_information", "needs_user_information")],
        "plan": [
            [{"name": "read_policy", "arguments": {"policy_id": "default"}}, {"name": "analyze_visual", "arguments": {"asset_id": "$asset", "question": "检查包装的可见破损与未知项"}}],
            [{"name": "evaluate_decision", "arguments": {"evidence_ids": "$evidence"}}],
            [{"name": "request_information", "arguments": {"fields": ["order_id", "photo"]}}],
        ],
    },
}


class RecordedClient:
    def __init__(self, provider):
        self.model = "recorded-" + provider
        self.cache_identity = self.model
        self.records = []

    def record(self):
        record_call(CallRecord(run_id=CURRENT_RUN.get(), provider="replay", model=self.model, request_id=new_id("replay"),
                              attempts=0, latency_ms=0, success=True), self.records)


class ReplayVLM(RecordedClient):
    def __init__(self, observations):
        super().__init__("vlm")
        # Sequential recordings cannot resume after arbitrary persistent cache hits.
        # Isolate replay sessions while retaining content reuse within one session.
        self.cache_identity = f"{self.model}:{new_id('session')}"
        self.observations = iter(observations)

    async def analyze(self, data, mime_type, *, question):
        from caselens.errors import ClientError
        try:
            observation = next(self.observations)
        except StopIteration as exc:
            raise ClientError("replay_exhausted", "recorded visual responses exhausted") from exc
        self.record()
        return VisualObservation.model_validate(observation)


class ReplayJev(RecordedClient):
    def __init__(self, decisions):
        super().__init__("jev")
        self.decisions = iter(decisions)

    async def classify(self, state, *, policy, evidence_ids):
        from caselens.clients import SUFFICIENCY
        from caselens.errors import ClientError
        try:
            category, sufficiency = next(self.decisions)
        except StopIteration as exc:
            raise ClientError("replay_exhausted", "recorded decision responses exhausted") from exc
        if category not in policy.categories:
            raise ClientError("replay_policy", "recorded scenario requires the default policy taxonomy")
        def prediction(choice, labels):
            return ChoicePrediction(choice=choice, probabilities={label: .9 if label == choice else .1/(len(labels)-1) for label in labels}, confidence=.73)
        self.record()
        return DecisionResult(category=prediction(category, policy.categories),
                              sufficiency=prediction(sufficiency, SUFFICIENCY), evidence_ids=evidence_ids,
                              policy_version=policy.version, model=self.model)


class ReplayController(RecordedClient):
    def __init__(self, plan):
        super().__init__("controller")
        self.plan = iter(plan)

    async def next_action(self, messages, *, tool_schemas):
        from caselens.errors import ClientError
        try:
            step = next(self.plan)
        except StopIteration as exc:
            raise ClientError("replay_exhausted", "recorded controller actions exhausted") from exc
        state = json.loads(messages[-1]["content"])["state"]
        originals = [asset for asset in state["assets"] if not asset["parent_asset_id"]]
        crops = [asset for asset in state["assets"] if asset["parent_asset_id"]]
        mapping = {"$asset": originals[0]["asset_id"], "$crop": crops[-1]["asset_id"] if crops else None,
                   "$evidence": [e["evidence_id"] for e in state["evidence"]]}
        calls = []
        for spec in step:
            arguments = {key: mapping.get(value, value) if isinstance(value, str) else value for key, value in spec["arguments"].items()}
            calls.append(ToolCall(call_id=new_id("call"), name=spec["name"], arguments=arguments))
        message = {"role": "assistant", "content": None, "tool_calls": [
            {"id": call.call_id, "type": "function", "function": {"name": call.name, "arguments": json.dumps(call.arguments)}} for call in calls]}
        self.record()
        return AgentAction(calls=calls, message=message)


def replay_clients(scenario: str):
    if scenario not in SCENARIOS:
        raise ValueError("scenario must be clear, blurred or missing")
    recording = SCENARIOS[scenario]
    return ReplayVLM(recording["observations"]), ReplayJev(recording["decisions"]), ReplayController(recording["plan"])


CUSTOMER_SCENARIOS = {"clear", "blurred", "missing", "irrelevant", "quantity", "multiple", "return", "wrong", "split"}


def recorded_prediction(choice, labels):
    return ChoicePrediction(choice=choice, probabilities={label: .9 if label == choice else .1/(len(labels)-1) for label in labels} if len(labels) > 1 else {choice: 1.}, confidence=.73)


class CustomerReplayJev(RecordedClient):
    def __init__(self, scenario):
        super().__init__("jev")
        self.scenario = scenario
        self.assessments = 0

    async def detect_intent(self, state):
        from caselens.schemas import ISSUES, IntentResult
        issues = ["return_request"] if self.scenario == "return" else ["wrong_item"] if self.scenario == "wrong" else ["missing_quantity"] if self.scenario in {"quantity", "split"} else ["packaging_issue", "missing_quantity"] if self.scenario == "multiple" else ["packaging_issue"]
        self.record()
        return IntentResult(primary=recorded_prediction(issues[0], [*ISSUES, "unclear"]),
                            issue_choices={issue: recorded_prediction("yes" if issue in issues else "no", ["yes", "no"]) for issue in ISSUES}, issues=issues, model=self.model)

    async def assess_issues(self, state, *, issues, policy, evidence_ids):
        from caselens.clients import SUFFICIENCY
        from caselens.schemas import GAPS, IssueFinding
        unresolved = self.scenario in {"missing", "irrelevant"} or (self.scenario == "blurred" and self.assessments == 0)
        gap = "photo_irrelevant" if self.scenario == "irrelevant" else "photo_blurry" if self.scenario == "blurred" and unresolved else "item_photo" if unresolved else "none"
        suff = "needs_visual_review" if gap == "photo_blurry" else "needs_user_information" if unresolved else "sufficient"
        # Deliberate contradictory fixture ensures the route gate does not blindly trust sufficiency.
        if self.scenario == "irrelevant":
            suff = "sufficient"
        self.assessments += 1
        self.record()
        return [IssueFinding(issue=issue, verification=recorded_prediction("insufficient_information" if unresolved else "verified", ["verified", "not_verified", "insufficient_information"]),
                             sufficiency=recorded_prediction(suff, SUFFICIENCY), gap=recorded_prediction(gap, GAPS), evidence_ids=evidence_ids, policy_version=policy.version, model=self.model,
                             supporting_materials={material: recorded_prediction("no" if unresolved else "yes", ["yes", "no"]) for material in ["relevant_photo", *(["order_details"] if issue in {"missing_quantity", "wrong_item"} else [])]}) for issue in issues]

    async def recommend(self, state, *, issue, policy, allowed_actions, evidence_ids):
        from caselens.schemas import Resolution
        choice = "reship" if "reship" in allowed_actions else "compensate" if "compensate" in allowed_actions else "human_review"
        self.record()
        return Resolution(issue=issue, action=recorded_prediction(choice, allowed_actions), evidence_ids=evidence_ids, policy_version=policy.version, model=self.model)


class CustomerReplayController(ReplayController):
    async def next_action(self, messages, *, tool_schemas):
        from caselens.schemas import AgentAction
        action = await super().next_action(messages, tool_schemas=tool_schemas)
        state = json.loads(messages[-1]["content"])["state"]
        observed_roots = {e["asset"]["root_asset_id"] for e in state["evidence"]}
        allowed = {schema["function"]["name"] for schema in tool_schemas}
        calls = []
        for call in action.calls:
            if call.name == "analyze_visual" and call.arguments["asset_id"] in observed_roots:
                # A resumed submission reuses its existing source-linked observations.
                continue
            if call.name == "finish" and "finish" not in allowed:
                call.name = "request_information"
                call.arguments = {"fields": ["order_id"] if not state["metadata"].get("order_id") else ["photo"]}
            calls.append(call)
        if not calls:
            calls = [ToolCall(call_id=new_id("call"), name="evaluate_issues", arguments={"evidence_ids": [e["evidence_id"] for e in state["evidence"]]})]
        message = {"role": "assistant", "content": None, "tool_calls": [
            {"id": call.call_id, "type": "function", "function": {"name": call.name, "arguments": json.dumps(call.arguments)}} for call in calls]}
        return AgentAction(calls=calls, message=message)


def customer_replay_clients(scenario):
    if scenario not in CUSTOMER_SCENARIOS:
        raise ValueError("unknown customer replay scenario")
    recording = SCENARIOS.get(scenario, SCENARIOS["clear"])
    observations = recording["observations"]
    if scenario == "irrelevant":
        observations = [{"facts": ["桌面上的笔记本与电脑，无法识别本次售后商品或包装"], "text": [], "unknowns": ["待售后商品状态不可见"]}]
    if scenario in {"quantity", "multiple"}:
        observations = [{"facts": ["订单明细显示应有三件，全部收到物品的照片中可见两件", "外包装可见破损"], "text": ["数量：3"], "unknowns": []}]
    if scenario == "return":
        observations = [{"facts": ["商品及附件出现在照片中，未见可观察破损"], "text": [], "unknowns": ["商家退货期限与实际收货时间未确认"]}]
    if scenario == "wrong":
        observations = [{"facts": ["订单截图写型号 A，实收商品标签写型号 B"], "text": ["订单型号 A", "实收型号 B"], "unknowns": []}]
    if scenario == "split":
        observations = [{"facts": ["订单数量3，已收包裹内可见2件，物流截图显示1件待到"], "text": ["数量3", "分包1件运输中"], "unknowns": []}]
    plan = []
    for step in recording["plan"]:
        converted = [{**call, "name": "evaluate_issues" if call["name"] == "evaluate_decision" else call["name"]} for call in step]
        plan.append(converted)
    if scenario == "irrelevant":
        plan[-1] = [{"name": "request_information", "arguments": {"fields": ["photo"]}}]
    if scenario == "split":
        plan.insert(1, [{"name": "check_quantity", "arguments": {"data": {"expected": 3, "received": 2, "pending": 1,
                      "order_evidence": True, "complete_photo": True, "evidence_ids": "$evidence"}}}])
        plan[-1] = [{"name": "escalate", "arguments": {"summary": "分包待到，需人工核实物流"}}]
        # Resolve structured evidence placeholders only inside this recorded fixture.
        class SplitController(CustomerReplayController):
            async def next_action(self, messages, *, tool_schemas):
                action = await super().next_action(messages, tool_schemas=tool_schemas)
                state = json.loads(messages[-1]["content"])["state"]
                for call in action.calls:
                    if call.name == "check_quantity":
                        call.arguments["data"]["evidence_ids"] = [e["evidence_id"] for e in state["evidence"]]
                action.message = {"role": "assistant", "content": None, "tool_calls": [
                    {"id": c.call_id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in action.calls]}
                return action
        return ReplayVLM(observations), CustomerReplayJev(scenario), SplitController(plan)
    return ReplayVLM(observations), CustomerReplayJev(scenario), CustomerReplayController(plan)
