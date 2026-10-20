import json

import httpx2
import pytest

from caselens.clients import JevClient
from caselens.config import JevConfig
from caselens.schemas import Policy


def answer(choice, labels):
    return {"type": "choice", "choice": choice, "probabilities": {label: .9 if label == choice else .1/(len(labels)-1) for label in labels}, "confidence": .75}


@pytest.mark.asyncio
async def test_intent_is_text_only_and_accepts_multiple_complaints():
    def serve(request):
        body = json.loads(request.content)
        assert body["state"]["user_text"] == "外箱破损且少了一件"
        assert "image_url" not in str(body)
        labels = list(body["questions"]["primary"]["criteria"])
        answers = {"primary": answer("missing_quantity", labels)}
        for name in body["questions"]:
            if name != "primary":
                answers[name] = answer("yes" if name in {"intent_packaging_issue", "intent_missing_quantity"} else "no", ["yes", "no"])
        return httpx2.Response(200, json={"model": "jev-test", "usage": {"input_tokens": 10, "output_tokens": 1}, "answers": answers})
    async with JevClient(JevConfig(api_key="test-key"), transport=httpx2.MockTransport(serve)) as client:
        result = await client.detect_intent({"user_text": "外箱破损且少了一件"})
    assert result.issues == ["packaging_issue", "missing_quantity"]
    assert result.primary.choice == "missing_quantity"


@pytest.mark.asyncio
async def test_issue_assessment_is_separate_from_intent_and_preserves_provenance():
    def serve(request):
        body = json.loads(request.content)
        answers = {}
        for name, question in body["questions"].items():
            choices = list(question["criteria"])
            selected = "verified" if name.endswith("verification") else "none" if name.endswith("gap") else "yes" if "material_" in name else "sufficient"
            answers[name] = answer(selected, choices)
        return httpx2.Response(200, json={"model": "jev-test", "usage": {"input_tokens": 10, "output_tokens": 1}, "answers": answers})
    async with JevClient(JevConfig(api_key="test-key"), transport=httpx2.MockTransport(serve)) as client:
        findings = await client.assess_issues({"evidence": []}, issues=["missing_quantity"], policy=Policy(), evidence_ids=["e1"])
    assert findings[0].issue == "missing_quantity"
    assert findings[0].verification.choice == "verified"
    assert findings[0].evidence_ids == ["e1"]


@pytest.mark.asyncio
async def test_remedy_uses_only_policy_allowed_candidates():
    def serve(request):
        body = json.loads(request.content)
        assert set(body["questions"]["action"]["criteria"]) == {"reship", "human_review"}
        return httpx2.Response(200, json={"model": "jev-test", "usage": {"input_tokens": 10, "output_tokens": 1}, "answers": {"action": answer("reship", ["reship", "human_review"])}})
    async with JevClient(JevConfig(api_key="test-key"), transport=httpx2.MockTransport(serve)) as client:
        result = await client.recommend({}, issue="missing_quantity", policy=Policy(), allowed_actions=["reship", "human_review"], evidence_ids=["e1"])
    assert result.action.choice == "reship"
    assert result.executed is False
    assert result.requires_human_approval is True
