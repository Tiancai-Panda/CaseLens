import json

import pytest

from caselens import context


def exchange(index, content):
    return [{"role": "assistant", "content": None, "tool_calls": [{"id": f"c{index}", "type": "function", "function": {"name": "analyze_visual", "arguments": "{}"}}]}, {"role": "tool", "tool_call_id": f"c{index}", "content": content}]


def test_long_history_is_compressed_but_skill_and_evidence_remain():
    manager = context.ContextManager(max_input_tokens=5000, output_reserve=500, recent_groups=2)
    state = {"assets": [{"asset_id": "a1", "parent_asset_id": None}], "evidence": [{"evidence_id": "e1", "asset": {"root_asset_id": "a1"}, "observation": {"facts": ["纸箱撕裂"], "text": ["订单标签" * 3000], "unknowns": ["内部不可见"]}}]}
    groups = [exchange(i, json.dumps({"ok": False, "error_code": "photo_blurry", "data": {"text": "x" * 10000}})) for i in range(8)]
    messages, stats, memory = manager.prepare(system="policy", goal="包装问题", state=state, schemas=[], groups=groups, skills=[{"name": "packaging", "version": "v1", "content": "必须保留包装与商品的区别"}])
    assert stats["estimated_input_tokens"] + 500 <= 5000
    assert stats["compacted_groups"] > 0
    assert "必须保留包装与商品的区别" in messages[0]["content"]
    current = json.loads(messages[-1]["content"])["state"]
    assert current["evidence"][0]["evidence_id"] == "e1"
    assert current["evidence"][0]["observation"]["unknowns"] == ["内部不可见"]
    assert "photo_blurry" in json.dumps(memory)
    assert memory["evidence_ids"] == ["e1"]


def test_message_trimming_preserves_complete_tool_call_groups():
    manager = context.ContextManager(max_input_tokens=4000, output_reserve=300, recent_groups=2)
    groups = [exchange(i, '{"ok":true}') for i in range(8)]
    messages, stats, _ = manager.prepare(system="rules", goal="检查", state={"evidence": []}, schemas=[], groups=groups, skills=[])
    assistants = [m for m in messages if m["role"] == "assistant"]
    ids = {c["id"] for m in assistants for c in m["tool_calls"]}
    assert len(assistants) == 2
    assert {m["tool_call_id"] for m in messages if m["role"] == "tool"} == ids
    assert stats["retained_groups"] == 2


def test_irreducible_context_overflow_is_explicit():
    manager = context.ContextManager(max_input_tokens=1000, output_reserve=100, recent_groups=0)
    with pytest.raises(context.ContextBudgetError):
        manager.prepare(system="R" * 2000, goal="检查", state={}, schemas=[], groups=[], skills=[])


def test_tool_schemas_count_towards_budget_and_no_dangling_results():
    manager = context.ContextManager(max_input_tokens=2000, output_reserve=200, recent_groups=2)
    with pytest.raises(context.ContextBudgetError):
        manager.prepare(system="rules", goal="检查", state={}, schemas=[{"description": "s" * 3000}], groups=[], skills=[])
    with pytest.raises(ValueError):
        manager.prepare(system="rules", goal="检查", state={}, schemas=[], groups=[[{"role": "tool", "tool_call_id": "dangling", "content": "ok"}]], skills=[])
