"""Deterministic reports cite accepted evidence instead of adding generated claims."""

import html
import os
from pathlib import Path
from urllib.parse import quote

from caselens.schemas import RunResult


def escape(value: str):
    text = html.escape(value).replace("\n", " ")
    for char in ["\\", "`", "*", "_", "[", "]", "#", "|"]:
        text = text.replace(char, "\\" + char)
    return text


def markdown(result: RunResult) -> str:
    lines = ["# CaseLens 分诊报告", "", f"运行模式：**{result.mode}**", ""]
    if result.mode == "replay":
        lines += ["> 离线录制回放：验证工程流程，不代表真实模型推理或分类准确率。", ""]
    lines += [f"状态：{result.status}", "", escape(result.summary), "", "## 证据", ""]
    if result.case_id:
        lines[4:4] = [f"工单：{result.case_id}；第 {result.submission} 次提交", ""]
    for evidence in result.evidence:
        source = quote(evidence.asset.source_path.replace(os.sep, "/"), safe=":/")
        lines += [f"### {evidence.evidence_id}", "",
                  f"来源：[{escape(Path(evidence.asset.source_path).name)}](<{source}>)",
                  f"区域：{evidence.asset.region or '原图'}；缓存：{evidence.cached}", ""]
        for name, values in [("可见事实", evidence.observation.facts), ("文字", evidence.observation.text), ("未知项", evidence.observation.unknowns)]:
            lines += [f"{name}：", ""] + [f"- {escape(value)}" for value in values] + [""]
    if result.decision:
        lines += ["## 决策", "", f"类别：{escape(result.decision.category.choice)}",
                  f"概率：{result.decision.category.probabilities}",
                  f"供应商 confidence（与类别概率分开）：{result.decision.category.confidence}",
                  f"证据充分性：{escape(result.decision.sufficiency.choice)}",
                  f"规则版本：{escape(result.decision.policy_version)}", ""]
    if result.intent:
        lines += ["## 用户诉求与核查", "", f"主诉：{result.intent.primary.choice}；涉及问题：{', '.join(result.intent.issues)}", ""]
        for finding in result.findings:
            lines += [f"- {finding.issue}：核实结果 {finding.verification.choice}；充分性 {finding.sufficiency.choice}；缺项 {finding.gap.choice}；证据 {', '.join(finding.evidence_ids)}", ""]
    if result.resolutions:
        lines += ["## 处理建议", "", "> 下列建议均未执行；资格、金额及发货数量需人工确认。", ""]
        lines += [f"- {r.issue}：{r.effective_action or r.action.choice}；Jev 原始选择 {r.action.choice}；规则版本 {escape(r.policy_version)}；模型 {escape(r.model)}；证据 {', '.join(r.evidence_ids)}" for r in result.resolutions]
        lines += [""]
    if result.supplement:
        lines += ["## 请补充材料", "", escape(result.user_message), ""]
    lines += ["## 执行记录", ""]
    lines += [f"运行时：{escape(result.runtime)}", ""]
    if result.skills:
        lines += ["场景 Skills：" + ", ".join(escape(s["name"]) + "@" + escape(str(s.get("version", "unknown"))) for s in result.skills), ""]
    if result.context_stats:
        stats = result.context_stats
        lines += [f"上下文估算：{stats.get('estimated_input_tokens')}；算法：{stats.get('estimator')}；压缩交互组：{stats.get('compacted_groups')}。此值不是供应商实测 Token 用量。", ""]
    lines += [f"- 第 {step.round} 轮：`{step.call.name}` → {'成功' if step.result.ok else step.result.error_code}" for step in result.steps]
    lines += ["", f"总耗时：{result.latency_ms:.1f} ms；记录调用：{len(result.calls)}。",
              "未取得完整用量时不报告精确成本；回放耗时不是模型延迟。", ""]
    return "\n".join(lines)


def save(result: RunResult, directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in [("result.json", result.model_dump_json(indent=2)), ("report.md", markdown(result))]:
        target = directory / name
        temporary = directory / (name + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
