# Customer After-sales Agent Implementation Plan

> **For agentic workers:** Execute inline using executing-plans; the user's implementation authorization is already present. Use read-only code review after integration.

**Goal:** Replace inspect/chat/batch/demo with a persistent customer-facing after-sales workflow.

**Architecture:** Preserve existing low-level clients/assets/registry; extend Jev with intent, per-issue verification and action Choices. A workflow service supplies legal tools and guarded outcomes. CaseStore owns managed image copies, submissions, policy snapshots and append-only run results, with an exclusive per-case run claim.

**Tech Stack:** Existing Python/Pydantic/SQLite/asyncio/Typer/Pillow/TypeSafe SDK; no new runtime dependencies.

**Spec:** docs/superpowers/specs/2026-10-07-customer-agent.md

## Global Constraints

- Six controller rounds; two supplementary analyses per original; eight active originals and eight submissions.
- Human-readable supplement instructions and resume on the same case.
- Remedies are suggestions with human approval required; never execute refunds.
- Named/inline API credentials excluded from case snapshots, logs and reports.
- No new eval work or Web front end; old diagnostic commands remain usable.

## Task 1: Typed business decisions and Jev transport

Files: schemas.py, clients.py, tests/test_customer_clients.py.
- [ ] Write transport tests for intent multi-question wire shape, issue findings, remedy candidate filtering and malformed distributions; run red.
- [ ] Add IntentResult, IssueFinding, Resolution, SupplementRequest and policy rules; implement detect_intent/assess_issues/recommend using official system_one.
- [ ] Run transport tests; preserve legacy classify behavior.

## Task 2: Guarded customer workflow

Files: customer.py, agent.py, prompts.py, replay.py, tests/test_customer.py.
- [ ] Test irrelevant-image prompts, multi-issue assessment, denied finish, quantity evidence requirements and budget fallback before implementation.
- [ ] Implement bootstrap intent, focused visual question generation, filtered tools, deterministic supplement instructions and validated remedy finalization.
- [ ] Extend Agent result collection without breaking legacy service tests; implement recorded customer Jev/controller fixtures.

## Task 3: Persistent human cooperation

Files: cases.py, service.py, tests/test_cases.py.
- [ ] Test restart restore, append/replace, new-evidence invalidation, immutable rule/mode, concurrent run claim and corrupt image rollback; run red.
- [ ] Store submission snapshots and runs transactionally; copy registered images under per-case root; restore crop references from saved evidence with canonical source validation.
- [ ] Implement create/resume/query APIs; persist interrupted-run failure and release exclusive claims.

## Task 4: CLI and delivery

Files: cli.py, report.py, README*, examples/customer-policy.json, tests/test_customer_cli.py.
- [ ] Test inspect text, resume/query, interactive same-case continuation, customer output and stage-specific probe errors before implementation.
- [ ] Wire all Agent entry points to shared workflow; update report/usage/docs, preserving diagnostic API and JSON stdout.
- [ ] Full tests/Ruff, read-only review, fix significant findings, wheel build/install and installed replay create/resume/query verification.

No Git repository exists; work in place, do not create commits or alter unrelated projects. Real business inference accuracy is not claimed from recorded fixtures.
