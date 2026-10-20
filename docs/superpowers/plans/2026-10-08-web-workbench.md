# CaseLens Web Workbench Implementation Plan

> **For agentic workers:** Execute inline with TDD. The approved design keeps the Web adapter thin and reuses the existing customer Agent.

**Goal:** Add a local FastAPI browser workbench for creating/resuming cases and observing Agent events without changing the CLI workflow.

**Architecture:** `web.py` exposes a small JSON API and serves an inline-friendly static HTML/JS dashboard. A per-process `WebJobManager` schedules `run_customer_case`, stores non-sensitive progress events in memory, and reuses `CaseStore` for durable cases. The Agent emits step events; the browser polls them. No new business logic, refund execution, or WebSocket dependency is introduced.

**Tech Stack:** FastAPI, Uvicorn, existing Python/Pydantic/asyncio/SQLite/Pillow/TypeSafe clients, vanilla HTML/CSS/JavaScript.

**Spec:** Approved conversation design on 2026-10-08; no separate Web frontend build system.

## Global Constraints

- Local-only Web server by default; bind to `127.0.0.1` in documented command.
- API keys remain server-side and never enter browser responses or events.
- Images are JSON Base64 uploads, max eight files and existing image limits apply.
- Existing CLI, replay mode, case persistence and policy gates remain compatible.
- Suggestions are not executed; no order-system write endpoints.

### Task 1: Agent event boundary

**Files:** `src/caselens/agent.py`, `src/caselens/cases.py`, `tests/test_web_events.py`.

- [x] Add a failing event-sink test.
- [x] Add optional async event sink with sanitized phase/tool/result events.
- [x] Thread the sink through `run_customer_case`; preserve existing callers.
- [x] Run event tests and all current tests.

### Task 2: FastAPI API and job manager

**Files:** create `src/caselens/web.py`, `tests/test_web_api.py`; modify `pyproject.toml`.

- [x] Add failing API tests for create/query/resume, corrupt uploads, cancellation and secret redaction.
- [x] Implement bounded job manager with one background event loop and shared live Runtime; graceful shutdown and 50-job retention.
- [x] Add job/event/SSE/case/list/image/report/resume/health endpoints.
- [x] Add and lock FastAPI/Uvicorn; credentials remain server-side.
- [x] Verify API and existing CLI tests.

### Task 3: Browser dashboard

**Files:** create `src/caselens/static/index.html`, `src/caselens/static/app.js`, `src/caselens/static/style.css`, `tests/test_web_ui.py`.

- [x] Replace initial source-text checks with real Playwright UI verification.
- [x] Implement vanilla browser workbench with uploads, previews, modes, SSE, results, history, supplements and JSON.
- [x] Distinguish task states and guard busy operations; recommendations never executed.
- [x] Verify desktop/mobile screenshots, no overflow, create/resume/history/image rendering.

### Task 4: Delivery and verification

**Files:** `README.zh-CN.md`, `README.md`, `docs/verification.md`, `examples/caselens.example.json` only if needed.

- [x] Document local startup on port 8765, config selection, SSE and replay walkthrough.
- [x] Build/install wheel, verify 130 tests/Ruff/JS syntax and Playwright desktop/mobile workflow; review secret redaction and shutdown.

No WebSocket, React, database service, Docker, or evaluation feature is added in this iteration.
