# Packaged scenario Skills implementation

Implemented 2026-10-12 in the WSL CaseLens project. This module does not make provider calls, execute after-sales actions, or change Policy.

## Integration status (2026-10-12)

Integrated into LangGraph load_skills, ToolService/CustomerService gates, CLI skills, Web /api/skills, protected context and reports. return/wrong/split replay scenarios are registered. Whole-project verification passed 203 tests, including Skills/quantity/return/split integration; model calls were replay/mock only.

See [Chinese guide](../PROJECT_GUIDE.zh-CN.md) for operation and [resume/interview notes](../PROJECT_RESUME.zh-CN.md) for explanations. The ownership and red/green observations below describe historical implementation steps, not outstanding integration work.

## Public contract

- SkillRegistry(root: Path | None = None) defaults exclusively to src/caselens/skill_bundles. An explicit root is a trusted administrator/test catalog, never a tool-selected executable directory.
- catalog() returns dictionaries containing name, description, version and issues.
- select(issues: list[str]) accepts business intent names and canonical Skill names, preserves request order, deduplicates each selected name, ignores unknown issues, and returns full SKILL.md text as name/version/content dictionaries.
- activate(name) accepts an enumerated catalog name and returns name/version/content/resources.
- read_resource(name, path) returns name/path/content for an allowlisted relative UTF-8 file.
- check_quantity(data) executes only the fixed shipped missing-quantity/scripts/check_quantity.py binding and returns status/missing_count/reason/evidence_ids.
- Invalid metadata, names, paths and helper data raise ValueError. Registry activation is stateless; ToolService owns active Skill and loaded-resource deduplication plus context protection.

## Scenario packages

Five self-authored packages provide standard YAML name/description frontmatter followed by Markdown. CaseLens supplemental manifest.json files carry version, issues, resource allowlists and fixed tool bindings.

| Package | Business intent |
| --- | --- |
| return-request | return_request |
| wrong-item | wrong_item |
| missing-quantity | missing_quantity |
| damaged-packaging | packaging_issue |
| product-issue | product_issue |

Each package includes an evidence checklist. The quantity package additionally includes split-shipment guidance, a JSON Schema, a worked multi-package example and the actual executable reconciliation helper. Instructions permit evidence reading and available image transformations to clarify observations, while retaining existing Policy, current legal-tool and human-approval gates. Return guidance does not invent merchant deadlines or issue a real return/refund.

## Boundaries

The loader rejects ambiguous YAML/JSON fields, invalid or mismatched names, empty descriptions/instructions, invalid versions and duplicate issue/resource entries. Discovery is capped at 32 root entries, 64 files and 512 KiB per bundle. Resource lists are capped at 32; each text read is bounded at 64 KiB. Paths reject absolute forms, backslashes, drive/URI separators, dot segments and non-allowlisted files. Symlinks are refused at discovery and read time, including a bundle directory replaced after discovery. Binary/non-UTF-8 reads fail. The trusted installed filesystem remains the trust boundary; this is not a sandbox for hostile concurrently modified installations.

The quantity helper accepts nullable expected/received/pending integers, strict boolean order_evidence/complete_photo flags, and bounded unique evidence IDs. Booleans cannot masquerade as integers; negative, fractional and over-one-million counts are rejected. Unknown fields are rejected. Unknown counts produce missing_evidence and a null missing_count; absent evidence never becomes a shortage decision.

With complete evidence, an excessive received-plus-pending total yields conflict; any positive pending quantity yields pending_shipment; a resolved deficit yields shortage; equality yields consistent. missing_count is arithmetic information, not an authorization. Evidence flags and IDs do not validate source contents by themselves: the integrating service must bind the arguments to actual validated case evidence, and all decisions remain Policy-gated.

## Verification evidence

Initial tests failed at collection because caselens.skills did not exist. After implementation, 15 tests passed. Added boundary tests exposed an unknown pending count incorrectly producing a numeric missing_count; it was fixed to retain null. A separate regression exposed a bundle-directory symlink replacement bypass; parent-directory checks fixed it.

Final isolated command:

    .venv/bin/pytest tests/test_skills.py -q
    .venv/bin/ruff check src/caselens/skills.py src/caselens/skill_bundles tests/test_skills.py

Result: 30 passed in 0.11 seconds; Ruff reported All checks passed.

Coverage includes actual bundle discovery and routing, full activation, resource reads, unknown names, malformed metadata, duplicate fields/resources, resource and directory symlink escapes, byte/file limits, trusted executable isolation, split shipment, shortage, consistency, conflicting totals, missing evidence, unknown quantities and malformed count/flag inputs.

Earlier intermediate Skills/return runs preceded replay integration. Final integration now includes split and return, and the published wheel includes all five Skill packages and their resources. No live-model validation was performed.
