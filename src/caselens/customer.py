"""Customer-facing issue routing, supplement instructions and policy suggestions."""

from caselens.prompts import CUSTOMER_CONTROL
from caselens.schemas import ACTIONS, ISSUES, SupplementRequest
from caselens.tools import (
    DEFINITIONS,
    DecisionArgs,
    FinishArgs,
    ToolFailure,
    ToolRegistry,
    ToolService,
)

CUSTOMER_TOOLS = {name: definition for name, definition in DEFINITIONS.items() if name != "evaluate_decision"}
CUSTOMER_TOOLS["evaluate_issues"] = (DecisionArgs, "Ask Jev to verify every identified complaint using all current evidence, policy and user claims.")
CUSTOMER_TOOLS["escalate"] = (FinishArgs, "Request Jev's human-review recommendation when judgments conflict or confidence is low.")
CUSTOMER_TOOLS["finish"] = (FinishArgs, "Ask Jev for policy-constrained after-sales recommendations and finish the report. This never executes refunds or shipments.")

CHECKLISTS = {
    "packaging_issue": "识别包装整体、封口、破损位置、是否有相关商品；不推断包装内部损坏。",
    "product_issue": "识别待售后商品及异常近照，描述裂痕、缺件等可见事实；照片不能证明功能故障。",
    "missing_quantity": "读取订单明细中的应有数量、全部收到物品的可见数量和分包信息；不能假设照片拍全。",
    "wrong_item": "读取订单规格和收到商品标签/型号/颜色，保留原文；不凭主张认定错发。",
    "return_request": "核对退货申请的订单、商品与附件、使用情况；不把退货要求认定为损坏，不推断购买日期或商家资格。",
}
GUIDANCE = {
    "photo_irrelevant": ("图片与本次商品或包装问题无关，无法据此核实。请重新拍摄。", ["拍摄待售后商品及外包装的整体照片。", "再拍摄问题部位近照，确保商品和异常位置清晰可见。"], ["photo"]),
    "photo_blurry": ("照片模糊或关键区域被遮挡，无法可靠核实。请重新拍摄清晰照片。", ["保持光线充足并对焦到异常位置。", "同时提供整体图和无遮挡的近照。"], ["photo"]),
    "order_details": ("还需要订单明细来核对数量或规格。", ["上传可读的订单商品、规格和购买数量截图。", "注明是否分包配送，遮盖不必要的姓名、地址等信息。"], ["order_details"]),
    "item_photo": ("还需要待售后商品或全部收到物品的照片。", ["把本次收到的物品放在同一画面内。", "数量不足时同时提供订单明细；错发时拍清商品标签。"], ["photo"]),
    "description": ("请说明希望解决的售后问题。", ["例如包装破损、商品损坏、少发几件或收到的规格不符。", "说明异常位置或现象；文字主张会与图片证据分别记录。"], ["description"]),
    "metadata": ("已获取相关图片，还需补充订单等必要信息才能形成处理建议。", ["填写订单编号及规则要求的信息，继续当前工单即可，无需重复上传图片。"], []),
}


class CustomerRegistry(ToolRegistry):
    definitions = CUSTOMER_TOOLS
    def schemas(self):
        allowed = self.service.allowed_tools()
        return [schema for schema in super().schemas() if schema["function"]["name"] in allowed]

    async def execute(self, call):
        from caselens.schemas import ToolResult
        # Validate using the shared registry's actual execution logic, including seen calls.
        if call.name not in CUSTOMER_TOOLS:
            return ToolResult(ok=False, error_code="unknown_tool", message="choose a customer workflow tool")
        if call.name not in self.service.allowed_tools():
            return ToolResult(ok=False, error_code="route_gate", message="action is not legal for the current state; use the supplied candidate tools")
        return await super().execute(call)


class CustomerService(ToolService):
    control_prompt = CUSTOMER_CONTROL

    def __init__(self, *args, user_text="", prior_evidence=None, analysis_counts=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_text = user_text
        self.intent = None
        self.findings = []
        self.resolutions = []
        self.supplement = None
        self.user_message = ""
        self.next_actions = []
        self.evidence = {e.evidence_id: e for e in prior_evidence or []}
        self.analysis_counts = dict(analysis_counts or {})
        self._registry = CustomerRegistry(self)

    async def bootstrap(self):
        self.intent = await self.jev.detect_intent({"user_text": self.user_text, "evidence": [e.observation.model_dump() for e in self.evidence.values()]})
        if self.intent.primary.choice == "unclear" or not self.intent.issues:
            await self.request_information(["description"])

    def state(self):
        return {**super().state(), "user_text": self.user_text, "intent": self.intent.model_dump() if self.intent else None,
                "issues": self.intent.issues if self.intent else [], "findings": [f.model_dump() for f in self.findings],
                "checklist": self.visual_question(), "allowed_next_tools": sorted(self.allowed_tools())}

    def visual_question(self):
        issues = self.intent.issues if self.intent else []
        return "只提取可观察事实、文字与未知项。" + " ".join(CHECKLISTS[issue] for issue in issues)

    def _all_analyzed(self):
        roots = {a.asset_id for a in self.assets.assets.values() if a.parent_asset_id is None}
        return bool(roots) and roots.issubset({e.asset.root_asset_id for e in self.evidence.values()})

    def _current_findings(self):
        return bool(self.intent and self.findings) and {f.issue for f in self.findings} == set(self.intent.issues) and all(
            set(f.evidence_ids) == set(self.evidence) and f.policy_version == self.policy.version for f in self.findings)

    def _eligible(self, finding):
        if finding.issue == "missing_quantity" and self.quantity_check and self.quantity_check["status"] != "shortage":
            return False
        required = ["relevant_photo", *(["order_details"] if finding.issue in {"missing_quantity", "wrong_item"} else [])]
        return (finding.verification.choice == "verified" and finding.verification.probabilities["verified"] >= self.settings.threshold
                and finding.sufficiency.choice == "sufficient" and finding.gap.choice == "none"
                and all(finding.supporting_materials.get(field) and finding.supporting_materials[field].choice == "yes" for field in required))

    def allowed_tools(self):
        if self.final_status:
            return set()
        allowed = {"request_information", "activate_skill"}
        if self.active_skills:
            allowed.add("read_skill_resource")
        if not self.intent or not self.intent.issues:
            return allowed
        if not self.policy_read:
            allowed.add("read_policy")
        if self.assets.assets:
            allowed.add("inspect_asset")
            allowed.update({"resize_image", "rotate_image"})
        if self.evidence:
            allowed.add("read_evidence")
            if "missing_quantity" in self.intent.issues:
                allowed.add("check_quantity")
        can_analyze = any(self.analysis_counts.get(a.root_asset_id, 0) < 1 + self.settings.max_supplements for a in self.assets.assets.values())
        if can_analyze:
            allowed.add("analyze_visual")
        if self._all_analyzed() and self.policy_read:
            allowed.add("evaluate_issues")
        if self._current_findings():
            allowed.add("escalate")
            if any(f.sufficiency.choice == "needs_visual_review" or f.gap.choice == "photo_blurry" for f in self.findings) and can_analyze:
                allowed.add("crop_image")
            if all(self._eligible(f) for f in self.findings) and not any(not self.metadata.get(field) for field in self.policy.required_metadata):
                allowed.add("finish")
        return allowed

    async def analyze_visual(self, asset_id, question):
        # The focus is fixed by the selected complaints, never replaced by a tool instruction.
        result = await super().analyze_visual(asset_id, question=self.visual_question() + " 局部检查问题：" + question)
        self.quantity_check = None
        self.findings, self.resolutions = [], []
        if hasattr(self, "checkpoint"):
            self.checkpoint()
        return result

    async def evaluate_issues(self, evidence_ids):
        if not self.policy_read or not self._all_analyzed() or set(evidence_ids) != set(self.evidence) or len(evidence_ids) != len(self.evidence):
            raise ToolFailure("dependency_missing", "read policy and analyze every active original; use all current evidence IDs")
        self.findings = await self.jev.assess_issues(self.business_state(), issues=self.intent.issues, policy=self.policy, evidence_ids=evidence_ids)
        if not self._current_findings():
            self.findings = []
            raise ToolFailure("invalid_decision", "findings do not match current issues, evidence and policy")
        self.resolutions = []
        self.revision += 1
        if hasattr(self, "checkpoint"):
            self.checkpoint()
        return {"findings": [f.model_dump() for f in self.findings], "allowed_next_tools": sorted(self.allowed_tools())}

    def business_state(self):
        return {"user_text": self.user_text, "user_asserted_metadata": self.metadata, "intent": self.intent.model_dump() if self.intent else None,
                "evidence": [{"evidence_id": e.evidence_id, "asset": e.asset.model_dump(), **e.observation.model_dump()} for e in self.evidence.values()],
                "findings": [f.model_dump() for f in self.findings], "policy": self.policy.model_dump(),
                "quantity_check": self.quantity_check}

    async def check_quantity(self, data):
        checked = await super().check_quantity(data)
        self.findings, self.resolutions = [], []
        self.revision += 1
        return checked

    async def finish(self, summary):
        if "finish" not in self.allowed_tools():
            raise ToolFailure("evidence_gate", "verified current evidence and required metadata are needed")
        return await self._resolve(review=False)

    async def escalate(self, summary):
        if not self._current_findings():
            raise ToolFailure("dependency_missing", "evaluate current evidence before escalation")
        return await self._resolve(review=True)

    async def _resolve(self, *, review):
        resolutions = []
        for finding in self.findings:
            allowed = self.policy.remedy_rules[finding.issue].allowed_actions
            if review or not self._eligible(finding):
                # A review option is mandatory in any policy, no forced financial suggestion.
                allowed = ["human_review"]
            resolution = await self.jev.recommend(self.business_state(), issue=finding.issue, policy=self.policy, allowed_actions=allowed, evidence_ids=list(self.evidence))
            if resolution.action.choice not in allowed or set(resolution.evidence_ids) != set(self.evidence) or resolution.policy_version != self.policy.version:
                raise ToolFailure("invalid_decision", "recommendation does not match allowed policy and provenance")
            if resolution.action.probabilities[resolution.action.choice] < self.settings.threshold:
                # Preserve raw model choice; the public workflow explicitly escalates rather than claiming approval.
                resolution.effective_action = "human_review"
            else:
                resolution.effective_action = resolution.action.choice
            resolutions.append(resolution)
        self.resolutions = resolutions
        review = review or any(r.effective_action == "human_review" for r in resolutions)
        self.final_status = "needs_review" if review else "completed"
        prefix = "以下依据演示政策，仅供测试。" if self.policy.is_demo else "以下为处理建议。"
        lines = [prefix]
        for finding, resolution in zip(self.findings, resolutions):
            lines.append(f"{ISSUES[finding.issue].split('，')[0]}：核查结果 {finding.verification.choice}；{ACTIONS[resolution.effective_action]}")
        if review:
            lines.append("当前结论需人工复核，尚未获准任何退款、补偿或发货。")
        lines.append("建议不等于执行；资格、金额和发货数量需人工确认。")
        self.user_message = self.summary = "\n".join(lines)
        self.next_actions = ["联系售后人员确认建议与订单信息。"]
        return {"status": self.final_status, "resolutions": [r.model_dump() for r in resolutions], "user_message": self.user_message}

    async def request_information(self, fields):
        allowed = {"photo", "description", "order_details", *self.policy.required_metadata}
        if not fields or any(field not in allowed for field in fields):
            raise ToolFailure("invalid_arguments", "request only supported supplement fields")
        reason = "description" if not self.intent or self.intent.primary.choice == "unclear" else "item_photo"
        if self.findings:
            gaps = [f.gap.choice for f in self.findings if f.gap.choice != "none"]
            if gaps:
                reason = gaps[0]
            elif any(f.issue in {"missing_quantity", "wrong_item"} and not (f.supporting_materials.get("order_details") and f.supporting_materials["order_details"].choice == "yes") for f in self.findings):
                reason = "order_details"
        message, instructions, required = GUIDANCE[reason]
        missing_metadata = [field for field in self.policy.required_metadata if not self.metadata.get(field)]
        if missing_metadata and self._current_findings() and all(self._eligible(f) for f in self.findings):
            reason = "metadata"
            message, instructions, required = GUIDANCE[reason]
        requested = list(dict.fromkeys([*required, *fields, *missing_metadata]))
        if missing_metadata:
            message += " 还请补充：" + "、".join(missing_metadata) + "。"
        self.supplement = SupplementRequest(reason=reason, fields=requested, message=message, instructions=instructions)
        self.requested_information = requested
        self.user_message = self.summary = message + "\n" + "\n".join(instructions)
        self.next_actions = instructions
        self.final_status = "needs_review"
        return {"status": self.final_status, "supplement": self.supplement.model_dump(), "user_message": self.user_message}

    async def on_budget(self):
        if not self.final_status:
            await self.request_information(["description"] if not self.intent or not self.intent.issues else ["photo"])

    def enrich_result(self, result):
        result.intent, result.findings, result.resolutions = self.intent, self.findings, self.resolutions
        result.supplement, result.user_message, result.next_actions = self.supplement, self.user_message, self.next_actions
