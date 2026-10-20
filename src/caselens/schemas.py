"""Internal contracts, independent of either model provider's wire format."""

import math
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AssetRef(Contract):
    asset_id: str
    root_asset_id: str
    content_hash: str
    source_path: str
    mime_type: str
    width: int
    height: int
    region: list[float] | None = None
    parent_asset_id: str | None = None
    derived_path: str | None = None
    transforms: list[dict[str, Any]] = Field(default_factory=list)


class VisualObservation(Contract):
    facts: list[str]
    text: list[str]
    unknowns: list[str]


class VisualEvidence(Contract):
    evidence_id: str = Field(default_factory=lambda: new_id("e"))
    asset: AssetRef
    observation: VisualObservation
    model: str
    prompt_version: str
    question: str
    cached: bool = False


class ChoicePrediction(Contract):
    choice: str
    probabilities: dict[str, float]
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def valid_distribution(self):
        if self.choice not in self.probabilities:
            raise ValueError("selected choice is missing from probabilities")
        values = list(self.probabilities.values())
        if not values or any(not math.isfinite(v) or not 0 <= v <= 1 for v in values):
            raise ValueError("probabilities must be finite values in [0, 1]")
        if not math.isclose(sum(values), 1, abs_tol=0.005):
            raise ValueError("probabilities must sum to approximately 1")
        if self.probabilities[self.choice] + 1e-8 < max(values):
            raise ValueError("selected choice must have the highest probability")
        return self


class Usage(Contract):
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None


class CallRecord(Contract):
    run_id: str | None = None
    provider: str
    request_id: str
    model: str
    attempts: int
    latency_ms: float
    success: bool
    usage: Usage = Field(default_factory=Usage)
    error_code: str | None = None


class DecisionResult(Contract):
    decision_id: str = Field(default_factory=lambda: new_id("d"))
    category: ChoicePrediction
    sufficiency: ChoicePrediction
    evidence_ids: list[str]
    policy_version: str
    model: str


ISSUES = {
    "packaging_issue": "用户主诉包装破损，不代表已核实。",
    "product_issue": "用户主诉商品损坏、质量或功能问题，不代表已核实。",
    "missing_quantity": "用户主诉收到数量少于订单数量，不代表已核实。",
    "wrong_item": "用户主诉错发型号、规格、颜色或商品，不代表已核实。",
    "return_request": "用户申请退货，不等于商品损坏，也不代表已满足商家退货资格。",
}
ACTIONS = {
    "refund": "建议退款，实际资格及金额须由人工确认。",
    "compensate": "建议补偿，实际金额须由人工确认。",
    "reship": "建议补发，实际商品与数量须由人工确认。",
    "human_review": "提交人工介入核实。",
}
GAPS = {
    "none": "没有缺少的材料。",
    "photo_irrelevant": "照片无法识别待售后商品或包装，与诉求无关。",
    "photo_blurry": "照片模糊、遮挡或关键区域不可见。",
    "order_details": "缺少可读订单明细或规格/数量依据。",
    "item_photo": "缺少商品及全部收到物品的相关照片。",
    "description": "诉求或故障描述不明确。",
}


class RemedyRule(Contract):
    allowed_actions: list[str] = Field(default_factory=lambda: ["human_review"])
    requirements: str = "仅提出建议；核实证据，不执行退款、补偿或补发。"

    @model_validator(mode="after")
    def valid_actions(self):
        if not self.allowed_actions or len(set(self.allowed_actions)) != len(self.allowed_actions) or any(action not in ACTIONS for action in self.allowed_actions):
            raise ValueError("allowed_actions must contain distinct supported recommendations")
        if "human_review" not in self.allowed_actions:
            raise ValueError("every remedy rule must allow human_review")
        return self


def demo_remedy_rules():
    return {
        "packaging_issue": RemedyRule(allowed_actions=["compensate", "human_review"], requirements="需看清包装破损；不推断内部商品损坏。"),
        "product_issue": RemedyRule(allowed_actions=["refund", "reship", "human_review"], requirements="需要相关商品照片与可核实的损坏；功能故障仅凭描述不得确认。"),
        "missing_quantity": RemedyRule(allowed_actions=["reship", "human_review"], requirements="必须同时有可读订单数量和全部收到商品的照片；用户自报数量只是主张，检查是否分包配送。"),
        "wrong_item": RemedyRule(allowed_actions=["reship", "refund", "human_review"], requirements="必须同时有订单规格与收到商品标签/型号的证据，不能仅凭用户描述确认。"),
        "return_request": RemedyRule(allowed_actions=["human_review"], requirements="核实订单、收货时间、商品使用与附件情况和实际商家退货规则；不编造期限或默认同意退款。"),
    }


class IntentResult(Contract):
    primary: ChoicePrediction
    issue_choices: dict[str, ChoicePrediction]
    issues: list[str]
    model: str


class IssueFinding(Contract):
    issue: str
    verification: ChoicePrediction
    sufficiency: ChoicePrediction
    gap: ChoicePrediction
    evidence_ids: list[str]
    policy_version: str
    model: str
    supporting_materials: dict[str, ChoicePrediction] = Field(default_factory=dict)


class Resolution(Contract):
    issue: str
    action: ChoicePrediction
    evidence_ids: list[str]
    policy_version: str
    model: str
    requires_human_approval: Literal[True] = True
    executed: Literal[False] = False
    effective_action: Literal["refund", "compensate", "reship", "human_review"] | None = None


class SupplementRequest(Contract):
    reason: str
    fields: list[str]
    message: str
    instructions: list[str]


class Policy(Contract):
    policy_id: str = "default"
    version: str = "after-sales-v1"
    categories: dict[str, str] = Field(default_factory=lambda: {
        "packaging_issue": "Visible packaging damage; product damage is not established.",
        "product_issue": "Visible damage to the product itself is established.",
        "insufficient_information": "Evidence does not establish either issue.",
    })
    required_metadata: list[str] = Field(default_factory=lambda: ["order_id"])
    remedy_rules: dict[str, RemedyRule] = Field(default_factory=demo_remedy_rules)
    is_demo: bool = True
    instructions: str = (
        "Separate visible facts from user assertions. Do not infer hidden damage. "
        "If unclear pixels could resolve the question choose needs_visual_review; "
        "if a missing order identifier or absent photo is needed choose needs_user_information. "
        "Only choose sufficient when the available evidence supports the category. "
        "This is triage only; never authorize a refund."
    )

    @model_validator(mode="after")
    def valid_taxonomy(self):
        # Backward-compatible old four-issue policies have no return eligibility rule.
        self.remedy_rules.setdefault("return_request", demo_remedy_rules()["return_request"])
        if len(self.categories) < 2 or any(not key.strip() or not value.strip() for key, value in self.categories.items()):
            raise ValueError("policy categories need at least two named labels with descriptions")
        if not self.version.strip() or not self.policy_id.strip():
            raise ValueError("policy_id and version are required")
        if set(self.remedy_rules) != set(ISSUES):
            raise ValueError("remedy_rules must define all supported complaint types")
        return self


class ToolCall(Contract):
    call_id: str
    name: str
    arguments: dict[str, Any]


class AgentAction(Contract):
    calls: list[ToolCall]
    message: dict[str, Any] = Field(default_factory=dict)


class ToolResult(Contract):
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    message: str | None = None
    retryable: bool = False


class StepRecord(Contract):
    round: int
    call: ToolCall
    result: ToolResult


class RunResult(Contract):
    runtime: str = "pipeline"
    skills: list[dict[str, Any]] = Field(default_factory=list)
    context_stats: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] = Field(default_factory=dict)
    run_id: str = Field(default_factory=lambda: new_id("run"))
    mode: Literal["live", "replay"]
    status: Literal["completed", "needs_review", "partial", "failed"]
    goal: str
    evidence: list[VisualEvidence] = Field(default_factory=list)
    decision: DecisionResult | None = None
    steps: list[StepRecord] = Field(default_factory=list)
    calls: list[CallRecord] = Field(default_factory=list)
    summary: str = ""
    requested_information: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: float = 0
    case_id: str | None = None
    submission: int | None = None
    intent: IntentResult | None = None
    findings: list[IssueFinding] = Field(default_factory=list)
    resolutions: list[Resolution] = Field(default_factory=list)
    supplement: SupplementRequest | None = None
    user_message: str = ""
    next_actions: list[str] = Field(default_factory=list)
