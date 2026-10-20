"""Bounded controller context; complete evidence remains in the case store."""

import copy
import json


class ContextBudgetError(ValueError):
    code = "context_budget_exceeded"


def estimate_tokens(value):
    # Conservative UTF-8 byte ceiling, not a provider tokenizer measurement.
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 64


class ContextManager:
    def __init__(self, *, max_input_tokens=24000, output_reserve=1200, recent_groups=3):
        if max_input_tokens <= output_reserve or recent_groups < 0:
            raise ValueError("context budget must exceed output reserve")
        self.budget, self.reserve, self.recent_groups = max_input_tokens, output_reserve, recent_groups

    @staticmethod
    def validate_groups(groups):
        for group in groups:
            if not group or group[0].get("role") != "assistant":
                raise ValueError("history must start with assistant tool calls")
            ids = [call["id"] for call in group[0].get("tool_calls", [])]
            outputs = [message.get("tool_call_id") for message in group[1:] if message.get("role") == "tool"]
            if not ids or len(set(ids)) != len(ids) or sorted(ids) != sorted(outputs) or len(outputs) != len(group) - 1:
                raise ValueError("history must contain complete matching tool results")

    @staticmethod
    def summarize(groups, state):
        actions, errors = [], []
        for group in groups:
            for call, message in zip(group[0]["tool_calls"], group[1:]):
                try:
                    output = json.loads(message["content"])
                except (TypeError, json.JSONDecodeError):
                    output = {"ok": False, "error_code": "unparsed_result"}
                code = output.get("error_code")
                actions.append({"tool": call["function"]["name"], "ok": output.get("ok"), "error_code": code})
                if code:
                    errors.append(code)
        return {"summary_version": "structured-v1", "actions": actions[-32:], "errors": list(dict.fromkeys(errors))[-16:],
                "evidence_ids": [e["evidence_id"] for e in state.get("evidence", [])],
                "key_evidence": [{"evidence_id": e["evidence_id"], "root_asset_id": e.get("asset", {}).get("root_asset_id"),
                                  "facts": [v[:160] for v in e.get("observation", {}).get("facts", [])[:3]],
                                  "unknowns": [v[:160] for v in e.get("observation", {}).get("unknowns", [])[:3]]} for e in state.get("evidence", [])],
                "current_issues": state.get("issues", []), "unresolved_findings": [{"issue": f["issue"], "gap": f.get("gap", {}),
                                       "verification": f.get("verification", {}), "evidence_ids": f.get("evidence_ids", [])}
                                      for f in state.get("findings", []) if f.get("gap", {}).get("choice") != "none" or f.get("verification", {}).get("choice") != "verified"],
                "policy_version": state.get("policy_version"), "revision": state.get("revision", 0),
                "pending": state.get("requested_information", []), "source": "recorded_actions_not_new_evidence"}

    @staticmethod
    def compact_state(state):
        projected = copy.deepcopy(state)
        for asset in projected.get("assets", []):
            asset.pop("source_path", None)
            asset.pop("derived_path", None)
        for evidence in projected.get("evidence", []):
            asset = evidence.get("asset", {})
            asset.pop("source_path", None)
            asset.pop("derived_path", None)
            observation = evidence.get("observation", {})
            omitted = {}
            for key in ("facts", "text", "unknowns"):
                items = observation.get(key, [])
                excerpt = [item[:240] for item in items[:6]]
                if excerpt != items:
                    omitted[key] = True
                observation[key] = excerpt
            evidence["excerpt_only"] = bool(omitted)
            evidence["omitted_fields"] = list(omitted)
        return projected

    def prepare(self, *, system, goal, state, schemas, groups, skills, prior_memory=None):
        self.validate_groups(groups)
        summary = self.summarize(groups, state)
        if prior_memory:
            summary["prior_actions"] = [*prior_memory.get("prior_actions", []), *prior_memory.get("actions", [])][-32:]
            summary["prior_errors"] = list(dict.fromkeys([*prior_memory.get("prior_errors", []), *prior_memory.get("errors", [])]))[-16:]
        protected = system + "\n" + "\n".join(f"Active skill {s['name']}@{s.get('version', 'unknown')}:\n{s['content']}" for s in skills)
        projected = self.compact_state(state)
        retained = list(groups[-self.recent_groups:]) if self.recent_groups else []
        initial_count = len(retained)
        while True:
            messages = [{"role": "system", "content": protected}, {"role": "user", "content": goal},
                        {"role": "user", "content": json.dumps({"memory": summary}, ensure_ascii=False)},
                        *[message for group in retained for message in group],
                        {"role": "user", "content": json.dumps({"state": projected}, ensure_ascii=False)}]
            count = estimate_tokens(messages) + estimate_tokens(schemas)
            if count + self.reserve <= self.budget:
                return messages, {"estimator": "utf8_byte_ceiling_v1", "estimated_input_tokens": count,
                                  "max_input_tokens": self.budget, "output_reserve": self.reserve,
                                  "compacted_groups": len(groups) - len(retained), "retained_groups": len(retained),
                                  "trimmed_for_budget": initial_count - len(retained)}, summary
            if retained:
                retained.pop(0)
                continue
            raise ContextBudgetError("protected instructions, schemas and evidence references exceed context budget")
