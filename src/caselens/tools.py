"""Per-run tool service: validated operations and evidence-gated finalization."""

import hashlib
import json
from typing import Any

from pydantic import Field, ValidationError

from caselens.assets import AssetStore
from caselens.config import Settings
from caselens.errors import ClientError
from caselens.prompts import VISUAL_VERSION
from caselens.schemas import Contract, Policy, ToolCall, ToolResult, VisualEvidence
from caselens.skills import SkillRegistry
from caselens.store import EvidenceStore


class InspectArgs(Contract):
    asset_id: str = Field(min_length=1)


class AnalyzeArgs(InspectArgs):
    question: str = Field(min_length=1, max_length=2000)


class CropArgs(InspectArgs):
    region: list[float] = Field(min_length=4, max_length=4)


class PolicyArgs(Contract):
    policy_id: str = Field(min_length=1)


class ResizeArgs(InspectArgs):
    width: int = Field(gt=0, le=30000, strict=True)
    height: int = Field(gt=0, le=30000, strict=True)


class RotateArgs(InspectArgs):
    angle: float = Field(ge=-360, le=360)


class EvidenceArgs(Contract):
    evidence_id: str = Field(min_length=1)
    field: str = "facts"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=5, ge=1, le=10)
    char_offset: int = Field(default=0, ge=0)


class DecisionArgs(Contract):
    evidence_ids: list[str] = Field(min_length=1, max_length=32)


class SkillArgs(Contract):
    name: str = Field(min_length=1, max_length=64)


class SkillResourceArgs(SkillArgs):
    path: str = Field(min_length=1, max_length=256)


class QuantityArgs(Contract):
    data: dict[str, Any]


class FinishArgs(Contract):
    summary: str = Field(min_length=1, max_length=1000)


class RequestArgs(Contract):
    fields: list[str] = Field(min_length=1, max_length=8)


DEFINITIONS = {
    "inspect_asset": (InspectArgs, "Read registered image metadata and provenance."),
    "analyze_visual": (AnalyzeArgs, "Extract visual evidence for a specific question; at most two supplements per original image."),
    "crop_image": (CropArgs, "Crop a registered image using normalized [x0,y0,x1,y1]; does not analyze it."),
    "resize_image": (ResizeArgs, "Resize a registered image through MCP; no new visual facts are created."),
    "rotate_image": (RotateArgs, "Rotate a registered image through MCP; angle is counterclockwise degrees."),
    "read_evidence": (EvidenceArgs, "Read a bounded page of original evidence facts/text/unknowns omitted from context."),
    "activate_skill": (SkillArgs, "Load a named bundled scenario Skill; unknown names are rejected."),
    "read_skill_resource": (SkillResourceArgs, "Read a named resource of an active Skill only when needed."),
    "check_quantity": (QuantityArgs, "Run missing-quantity's trusted deterministic count check; data counts are candidates, not proof. Needs actual evidence IDs."),
    "read_policy": (PolicyArgs, "Read the configured triage policy before making a decision."),
    "evaluate_decision": (DecisionArgs, "Use Jev to judge all current evidence after policy and visual results exist."),
    "finish": (FinishArgs, "Finish only with current sufficient evidence and a high-probability decision; never execute refunds."),
    "request_information": (RequestArgs, "End with needs_review and ask for named missing fields (order_id, photo, description or policy-required fields)."),
}


class ToolFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ToolRegistry:
    definitions = DEFINITIONS
    def __init__(self, service: "ToolService"):
        self.service = service
        self._seen: set[str] = set()

    def schemas(self) -> list[dict]:
        declarations = [{"type": "function", "function": {"name": name, "description": description,
                 "parameters": model.model_json_schema()}} for name, (model, description) in self.definitions.items()]
        image_client = getattr(self.service, "image_client", None)
        if image_client:
            discovered = {s["function"]["name"]: s for s in image_client.schemas()}
            declarations = [discovered.get(s["function"]["name"], s) for s in declarations]
        return declarations

    async def execute(self, call: ToolCall) -> ToolResult:
        if call.name not in self.definitions:
            return ToolResult(ok=False, error_code="unknown_tool", message="choose a registered tool")
        try:
            arguments = self.definitions[call.name][0].model_validate(call.arguments)
        except ValidationError:
            return ToolResult(ok=False, error_code="invalid_arguments", message="arguments do not match the tool schema")
        version = self.service.revision if call.name in {"evaluate_decision", "evaluate_issues", "finish", "escalate"} else 0
        signature = hashlib.sha256(json.dumps([call.name, arguments.model_dump(), version], sort_keys=True).encode()).hexdigest()
        if signature in self._seen:
            return ToolResult(ok=False, error_code="repeated_call", message="this successful call was already made; choose a new question or action")
        self._seen.add(signature)
        try:
            data = await getattr(self.service, call.name)(**arguments.model_dump())
            return ToolResult(ok=True, data=data)
        except ToolFailure as exc:
            result = ToolResult(ok=False, error_code=exc.code, message=str(exc))
        except ClientError as exc:
            result = ToolResult(ok=False, error_code=exc.code, message=exc.message, retryable=exc.retryable)
        except (ValueError, OSError):
            result = ToolResult(ok=False, error_code="invalid_input", message="input is unavailable, changed, or invalid")
        self._seen.discard(signature)
        return result


class ToolService:
    def __init__(self, assets: AssetStore, vlm, jev, cache: EvidenceStore, policy: Policy,
                 metadata: dict[str, Any], settings: Settings):
        self.assets, self.vlm, self.jev, self.cache = assets, vlm, jev, cache
        self.policy, self.metadata, self.settings = policy, metadata, settings
        self.evidence: dict[str, VisualEvidence] = {}
        self.decision = None
        self.policy_read = False
        self.revision = 0
        self.analysis_counts: dict[str, int] = {}
        self.final_status = None
        self.summary = ""
        self.requested_information: list[str] = []
        self._registry = ToolRegistry(self)
        self.skill_registry = SkillRegistry()
        self.active_skills = []
        self.skill_resources = []
        self.quantity_check = None

    def registry(self):
        return self._registry

    def state(self):
        return {"assets": [a.model_dump() for a in self.assets.assets.values()],
                "evidence": [e.model_dump() for e in self.evidence.values()],
                "decision": self.decision.model_dump() if self.decision else None,
                "metadata": self.metadata, "policy_read": self.policy_read,
                "analysis_counts": self.analysis_counts, "revision": self.revision}

    async def activate_skill(self, name):
        activated = self.skill_registry.activate(name)
        if not any(s["name"] == name for s in self.active_skills):
            self.active_skills.append({key: activated[key] for key in ("name", "version", "content")})
        return activated

    async def read_skill_resource(self, name, path):
        if not any(s["name"] == name for s in self.active_skills):
            raise ToolFailure("skill_inactive", "activate this bundled Skill before reading resources")
        resource = self.skill_registry.read_resource(name, path)
        if not any(r["name"] == name and r["path"] == path for r in self.skill_resources):
            self.skill_resources.append(resource)
        return resource

    async def check_quantity(self, data):
        ids = data.get("evidence_ids", [])
        if (not isinstance(ids, list) or not ids or len(ids) > 64 or any(not isinstance(eid, str) for eid in ids)
                or len(set(ids)) != len(ids) or not set(ids).issubset(self.evidence)):
            raise ToolFailure("invalid_evidence", "quantity input must cite existing current evidence")
        await self.activate_skill("missing-quantity")
        checked = self.skill_registry.check_quantity(data)
        checked["candidate_counts_not_verified"] = True
        checked["inputs"] = data
        self.quantity_check = checked
        return checked

    async def inspect_asset(self, asset_id: str):
        return self.assets.get(asset_id).model_dump()

    async def read_policy(self, policy_id: str):
        if policy_id != self.policy.policy_id:
            raise ToolFailure("unknown_policy", "choose the configured policy_id")
        self.policy_read = True
        return self.policy.model_dump()

    async def analyze_visual(self, asset_id: str, question: str):
        asset = self.assets.get(asset_id)
        count = self.analysis_counts.get(asset.root_asset_id, 0)
        if count >= 1 + self.settings.max_supplements:
            raise ToolFailure("supplement_limit", "visual analysis budget exhausted for this original image")
        self.analysis_counts[asset.root_asset_id] = count + 1
        # Verify immutable input even on a cache hit.
        data = self.assets.data(asset_id)
        key = self.cache.key(asset.content_hash, self.vlm.cache_identity, VISUAL_VERSION, question)
        observation = self.cache.get(key)
        cached = observation is not None
        if observation is None:
            observation = await self.vlm.analyze(data, asset.mime_type, question=question)
            self.cache.put(key, observation)
        evidence = VisualEvidence(asset=asset, observation=observation, model=self.vlm.model,
                                  prompt_version=VISUAL_VERSION, question=question, cached=cached)
        self.evidence[evidence.evidence_id] = evidence
        self.revision += 1
        self.decision = None
        return evidence.model_dump()

    async def crop_image(self, asset_id: str, region: list[float]):
        if getattr(self, "image_client", None):
            return await self._image_transform("crop_image", {"asset_id": asset_id, "region": region})
        # Direct service calls outside the Agent retain their legacy local behavior.
        return self.assets.crop(asset_id, region).model_dump()

    async def _image_transform(self, name, arguments):
        if not getattr(self, "image_client", None):
            raise ToolFailure("mcp_unavailable", "image MCP is not connected")
        try:
            return (await self.image_client.call(name, arguments)).model_dump()
        except RuntimeError as exc:
            raise ToolFailure("mcp_transport", "image MCP transport failed or timed out") from exc

    async def resize_image(self, asset_id, width, height):
        return await self._image_transform("resize_image", {"asset_id": asset_id, "width": width, "height": height})

    async def rotate_image(self, asset_id, angle):
        return await self._image_transform("rotate_image", {"asset_id": asset_id, "angle": angle})

    async def read_evidence(self, evidence_id, field="facts", offset=0, limit=5, char_offset=0):
        if evidence_id not in self.evidence or field not in {"facts", "text", "unknowns"}:
            raise ToolFailure("invalid_arguments", "select an existing evidence ID and observation field")
        values = getattr(self.evidence[evidence_id].observation, field)
        return {"evidence_id": evidence_id, "field": field, "offset": offset,
                "char_offset": char_offset, "values": [v[char_offset:char_offset+2000] for v in values[offset:offset+limit]], "total": len(values),
                "next_char_offset": char_offset + 2000 if any(len(v) > char_offset + 2000 for v in values[offset:offset+limit]) else None,
                "excerpt_only": any(len(v) > char_offset + 2000 for v in values[offset:offset+limit])}

    async def evaluate_decision(self, evidence_ids: list[str]):
        if not self.policy_read:
            raise ToolFailure("dependency_missing", "read_policy first")
        if set(evidence_ids) != set(self.evidence) or len(evidence_ids) != len(self.evidence):
            raise ToolFailure("dependency_missing", "pass all current evidence_ids, without duplicates")
        roots = {a.asset_id for a in self.assets.assets.values() if a.parent_asset_id is None}
        seen_roots = {e.asset.root_asset_id for e in self.evidence.values()}
        if not roots.issubset(seen_roots):
            raise ToolFailure("dependency_missing", "analyze every original input image before classifying")
        evidence = [self.evidence[eid] for eid in evidence_ids]
        state = {"evidence": [{"evidence_id": e.evidence_id, "asset_id": e.asset.asset_id,
                              "region": e.asset.region, **e.observation.model_dump()} for e in evidence],
                 "metadata": self.metadata, "policy": self.policy.model_dump()}
        decision = await self.jev.classify(state, policy=self.policy, evidence_ids=evidence_ids)
        if set(decision.evidence_ids) != set(evidence_ids) or decision.policy_version != self.policy.version:
            raise ToolFailure("invalid_decision", "decision provenance does not match current evidence and policy")
        self.decision = decision
        return decision.model_dump()

    async def finish(self, summary: str):
        decision = self.decision
        missing = [field for field in self.policy.required_metadata if not self.metadata.get(field)]
        if decision is None or set(decision.evidence_ids) != set(self.evidence) or missing:
            raise ToolFailure("evidence_gate", "a current decision and required metadata are needed before finishing")
        probability = decision.category.probabilities[decision.category.choice]
        if (decision.sufficiency.choice != "sufficient" or probability < self.settings.threshold
                or decision.category.choice == "insufficient_information"
                or not any(e.observation.facts or e.observation.text for e in self.evidence.values())):
            raise ToolFailure("evidence_gate", "decision is uncertain or evidence is insufficient; supplement or request information")
        self.final_status = "completed"
        # A free-form controller summary is not evidence; generate the public conclusion from contracts.
        self.summary = f"分诊类别：{decision.category.choice}；类别概率：{probability:.3f}。依据：{', '.join(decision.evidence_ids)}。仅供人工分诊，不执行退款。"
        return {"status": self.final_status, "summary": self.summary, "evidence_ids": decision.evidence_ids}

    async def request_information(self, fields: list[str]):
        allowed = {"photo", "description", *self.policy.required_metadata}
        if any(field not in allowed for field in fields):
            raise ToolFailure("invalid_arguments", "request only policy-required metadata, photo or description")
        self.final_status = "needs_review"
        self.requested_information = list(dict.fromkeys(fields))
        self.summary = "需要补充信息或人工复核：" + ", ".join(self.requested_information)
        return {"status": self.final_status, "requested_information": self.requested_information}
