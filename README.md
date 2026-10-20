# CaseLens

[中文使用说明](README.zh-CN.md) · [Project guide (Chinese)](PROJECT_GUIDE.zh-CN.md) · [Resume and interview Q&A (Chinese)](PROJECT_RESUME.zh-CN.md)

CaseLens is an evidence-grounded after-sales Agent with Web and CLI entry points. A VLM extracts visual facts, TypeSafe Jev makes typed decisions over structured evidence and policy, and a controller selects native function calls. Suggestions are never executed as real refunds or shipments.

## Current Runtime (2026-10-12)

The updated project is in WSL Ubuntu at `/home/tcpanda/projects/caselens`. The Windows copy was not updated.

- LangGraph 0.6.11 StateGraph: bootstrap → load_skills → prepare_context → controller → tools → checkpoint → loop/finalize.
- MCP SDK 1.30.0: real stdio discovery and crop/resize/rotate calls with persistent derived images and provenance.
- Five packaged scenario Skills; missing-quantity includes reference files, JSON Schema, an example and a trusted deterministic helper.
- Bounded controller context, structured action/error summaries, complete tool exchange groups, evidence excerpts and same-case memory.
- SQLite case checkpoints and SSE runtime events; no native LangGraph checkpointer or interrupt-based approval is claimed.

Jev receives structured text/state, not pixels. Probability and provider confidence are separate. Skills cannot override Policy or tool validation.

## Install / Refresh

Python 3.11+ and uv:

```bash
uv sync --locked --extra dev --no-editable --cache-dir .uv-cache
uv sync --locked --extra dev --no-editable --cache-dir .uv-cache --reinstall-package caselens-agent
.venv/bin/caselens --help
```

Use the Linux virtual environment, not Windows Scripts executables. The second command refreshes a non-editable install after source changes.

## Offline Demonstration

Jev is currently unavailable; use replay rather than live probes.

```bash
cd /home/tcpanda/projects/caselens
.venv/bin/caselens demo --scenario clear
.venv/bin/caselens demo --scenario blurred
.venv/bin/caselens demo --scenario split
.venv/bin/caselens demo --scenario return
.venv/bin/caselens skills --name missing-quantity
```

Scenarios: clear, blurred, missing, irrelevant, quantity, multiple, return, wrong, split. Demo creates a synthetic image and saves report.md/result.json under outputs/demo/<scenario>. Model responses are fixtures; they do not measure image understanding. Local image MCP calls still use actual subprocesses.

## Local Web

```bash
CASELENS_WEB_CONFIG=./caselens.wsl.json \
  .venv/bin/python -m uvicorn caselens.web:app --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765/>. Choose a replay scenario while Jev is unavailable. Substitute your actual config path; otherwise Web defaults to caselens.json. CLI and Web need the same state_dir to share cases. No frontend build is required; Node/Playwright is only needed for browser tests. Keep the unauthenticated prototype local-only.

## Live Mode After Service Recovery

Fill actual endpoints, models and api_key fields in a local config based on examples/caselens.example.json. Inline keys take precedence over named environment variables. Do not share credential files.

```bash
.venv/bin/caselens doctor --config ./caselens.wsl.json
# Real provider calls, potentially billable:
.venv/bin/caselens doctor --config ./caselens.wsl.json --probe
.venv/bin/caselens inspect ./photo.jpg --text "The packaging is damaged" \
  --order-id ORDER-001 --config ./caselens.wsl.json --output outputs/live-case
```

doctor without --probe checks configuration only. inspect runs the customer Agent; analyze is single-stage extraction and classify is a fixed pipeline. resume continues a case; case --history reads saved runs. skills inspects packaged instructions. mcp-server --root /allowed/images serves an external stdio Host; the Agent already manages its own MCP process.

## Verification and Limits

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/caselens tests
.venv/bin/python -m pip check
```

The last complete runtime verification passed 203 tests, lint, dependency checks and wheel build. Tests use replay/mock model responses and real local MCP, not live Jev/VLM/controller calls or labeled accuracy evaluation. See [verification record](docs/verification.md) for current documentation checks and browser-test limitations.

At most six controller rounds and two additional analyses per original image; parameters, legal actions, paths, geometry and evidence references are validated. Client transport retries are not multiplied by graph retries. Context budgeting uses a conservative UTF-8 byte ceiling, not exact provider tokenization. Same-case memory is not RAG or cross-customer long-term memory.

Not implemented: PDF/Office, authenticated multi-tenant deployment, distributed scheduling, real order writes or automatic refunds. [Six project proposals](docs/project-options.md) are design alternatives, not six delivered products. Resume claims must not invent accuracy, calibration, latency or cost improvements.
