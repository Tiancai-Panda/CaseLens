# GitHub Engineering Research and Project Options

> 2026-10-12 状态更新：本页是六个候选方案与原调研记录，不代表六套产品全部实现。推荐 B 的 CaseLens 已进一步完成 Web/CLI、LangGraph、图片 MCP、5 类 Skills、上下文与工单记忆。当前 MCP SDK 实际锁定为 1.30.0，不能把本页早期 2.x 建议当成代码版本。最新已实现范围、运行方法及验证边界以 [当前规格](design.md)、[项目指南](../PROJECT_GUIDE.zh-CN.md) 和 [验证记录](verification.md) 为准。原 GitHub 快照、阅读限制和未来方向保留，不改写为本轮新调研或真实运行结果。


Research snapshot: 2026-10-07. Star counts and push dates are snapshots, not quality guarantees. GitHub READMEs, repository trees and selected source files were inspected without installing or executing reference projects. A check mark means behavior was found in inspected code; △ means README/example/directory support without full implementation verification.

## Jev: role and boundary

The user confirmed TypeSafe AI's Jev, not JEPA. The [official announcement](https://typesafe.ai/blog/introducing-system-one-models-and-jev) is dated September 15, 2026, rather than September 22. [Official docs](https://docs.typesafe.ai/introduction) define Choice (typed choice plus probabilities/confidence), Score and Noul (truth probability) over text/structured state. Jev does not accept image pixels as its decision input. The official Python SDK ([repository](https://github.com/typesafe-ai/typesafe-sdk-python), 272 stars) exposes TypeSafeClient.system_one(state, questions); inspected SDK 0.7.2 also exposes AsyncTypeSafeClient and retry configuration.

Use image → VLM facts/text/unknowns → structured state plus policy → Jev decision → controller action → report. Keep Jev confidence separate from choice probability. Typed output does not guarantee a correct business decision.

## GitHub projects

| Project | Snapshot / status | Inspected code and engineering lesson | Model substitution |
|---|---|---|---|
| [huggingface/smolagents](https://github.com/huggingface/smolagents) | 29.7k stars; active; Apache-2.0 | agents.py: bounded loop, memory, planning, errors and final checks. tools.py: schemas and argument validation. models.py: provider adapter/rate limit/retry. mcp_client.py: connection lifecycle. Tool calls may use ThreadPoolExecutor, not an asyncio-native loop. | Keep loop/registry, replace inference adapter with VLM/controller API and Jev decision tool. Do not copy arbitrary Python execution into this project. |
| [HKUDS/nanobot](https://github.com/HKUDS/nanobot) | 48.8k stars; active; MIT | agent/tools/registry.py: registration, schema cache, prepare/validate/execute. agent/loop.py: budgeted loop and session state. agent/tools/mcp.py: cleanup, reconnection, timeout and image blocks. cli/commands.py: Typer/config/logging. | Reuse small boundaries, not the personal-assistant platform. Keep model APIs in clients and expose model decisions as tools. |
| [lastmile-ai/mcp-agent](https://github.com/lastmile-ai/mcp-agent) | 8.6k stars; last push Jan 25, 2026; Apache-2.0 | agents/agent.py: async lifecycle and LLM attachment. mcp/mcp_aggregator.py: parallel discovery, server-qualified names, per-server errors and call dispatch. | Put VLM/Jev behind MCP tools. Useful patterns, but maintenance snapshot is older than other choices. |
| [docling-project/docling](https://github.com/docling-project/docling) | 68.5k stars; active; MIT | document_converter.py: format dispatch, common result, page/file bounds, optional threaded conversion. pipeline/vlm_pipeline.py: page batches/resources and remote/local boundary; older VLM options are deprecated. | Reuse the document/evidence abstraction. MVP can use a light parser/image input; do not install a full local model pipeline without need. |
| [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) | about 107k stars; Apache-2.0 | tools/tool-registry.ts: function declarations, discovered tools and MCP qualified names. tools/read-file.ts: defensive path resolution and operation logging. README verifies headless JSON/streaming and MCP. | Recreate product-facing CLI/output conventions in Python; use your own VLM/Jev APIs. |
| [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | about 24.5k stars; MIT | client/client.py: context-managed async list_tools/call_tool. Current README uses MCPServer/Client; old mcp.server.fastmcp.FastMCP is removed in SDK 2.x. | Use a pinned compatible SDK only when adding MCP. MCP is transport/tool discovery, not an Agent planner. |
| [typesafe-ai/system-one-adapter-python](https://github.com/typesafe-ai/system-one-adapter-python) | 381 stars; pushed Sep 22, 2026; MIT | _client.py: official-style interface backed by ordinary LLMs; separates transient and malformed-output retries and records diagnostics. This is not Jev itself. | Use as an optional LLM baseline; do not call LLM self-reported probabilities calibrated. |

Additional README/structure research: [LiteLLM](https://github.com/BerriAI/litellm) offers unified provider SDK/gateway; its Router implementation and licensing were not fully audited, so it is not a default dependency. [Landing AI vision-agent](https://github.com/landing-ai/vision-agent) demonstrates plan/tool/test loops but its repository is deprecated; retain it as historical architecture reference only.

| Project | Multi-model | Prompt | Function calling | MCP | CLI | Files | Async | Parallel | Retry | Logging | Structured output |
|---|---|---|---|---|---|---|---|---|---|---|---|
| smolagents | ✓ | ✓ | ✓ | ✓ | △ | △ | △ | ✓ threads | ✓ | ✓ | ✓ |
| nanobot | △ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | △ | ✓ | ✓ |
| mcp-agent | △ | ✓ | △ | ✓ | △ | △ | ✓ | ✓ discovery | △ | ✓ | ✓ tools |
| Docling | ✓ | △ | — | △ | △ | ✓ | △ | ✓ threads | — | ✓ | ✓ |
| Gemini CLI | △ | △ | ✓ declarations | △ | ✓ | ✓ | ✓ | △ | △ | ✓ | ✓ |
| MCP Python SDK | — | △ prompts | — | ✓ | △ | — | ✓ | — | — | △ | ✓ |
| System One Adapter | ✓ baseline | ✓ | — | — | — | — | ✓ | △ | ✓ | △ diagnostics | ✓ |

## Shared engineering contract

Internal APIs: async VLMClient.analyze(asset, instruction, schema), async JevClient.classify(state, decision_spec), and controller next_action(messages, tool_schemas). VLM observes; Jev decides over structured state; controller selects native tools. Validate schemas before execution. Persist tool steps, short action summaries, evidence IDs and result state, not hidden chain-of-thought.

Default limits: 6 controller rounds, 2 additional visual analyses per original image, 8 calls/round, 60-second VLM/controller timeout, 15-second Jev timeout, at most 2 retries, per-provider concurrency/RPM, and 30-second circuit cooldown after 5 transient failures. New observations invalidate old decisions. Finalization requires current evidence, policy and metadata, Jev sufficiency, factual evidence, and default category probability threshold 0.80. This is configurable and not a calibrated guarantee.

Use one process, one shared service, versioned cache keys and SQLite only for resumable batches. Add MCP only for cross-host reuse; FastAPI only for HTTP clients; OCR only if VLM text extraction proves inadequate; Redis/Celery/Kafka/Kubernetes only when measured needs require them. Keys are named environment variables, stdout is JSON, stderr is logs, and remote API URLs require HTTPS.

## Six distinct proposals

### 1. CaseLens — after-sales evidence triage Agent

Problem: triage damage reports using pictures, order identifiers and a policy. Flow: VLM facts → Jev category/sufficiency → controller selects finish, crop/reinspect or request information → cited report. Agent behavior: open-ended native tool calls, bounded follow-up, stale-decision invalidation and explicit human review; never issues refunds. MVP (1–3 days): one image, three categories, VLM/real Jev, JSON; then tool loop/crop/policy/report. Stack: Python, Pydantic, httpx/provider SDK, asyncio, Pillow and SQLite cache. Resume value: native tool calling, observable recovery, and separate visual/decision error analysis.

### 2. VisionDesk MCP — reusable multimodal tool server

Problem: expose image evidence, typed classification and report tools to more than one MCP Host. Start with one server; split artifact tools only when useful. Flow: MCP discovery → controller maps schema to native tools → shared service → VLM → Jev → structured result. MVP: VLM/Jev tools, client discovery/call, second Host check. Stack: Python, pinned MCP SDK, Pydantic, VLM/Jev clients. Resume value: protocol adapter, discovery, namespaces, validation. MCP gives interoperability, not Agent planning.

### 3. DocCheck — multimodal acceptance-document auditor

Problem: find missing/inconsistent evidence in a PDF/image acceptance package. Start with PDF text and page rendering; DOCX/PPTX/XLSX later. Flow: parser → source-linked text/page/image evidence → Agent selects pages → VLM reads visual evidence → Jev checks list → page-referenced report. MVP: PDFs plus images, three checks, provenance. Stack: PyMuPDF, Pillow, Pydantic, VLM/Jev, asyncio. Resume value: unified evidence model, routing, cross-page follow-up. Do not claim office/archive support until implemented; ZIP must enforce path/count/size limits.

### 4. AssetLint — e-commerce asset preflight CLI

Problem: classify and preflight product listing images. Explicit classify is fixed workflow; open inspect uses an Agent. Commands: analyze, classify, inspect, batch, chat, doctor. MVP: Typer single image and JSON, then folder/JSONL. Stack: Python, Typer/Rich, Pillow, Pydantic, VLM/Jev. Resume value: installable CLI, stdout/stderr separation, stable exit codes and batch resume. Most reliable one-week delivery.

### 5. TriageBatch — recoverable visual triage worker

Problem: process a large directory under API quotas and recover after interruption. Flow: manifest/dedup → bounded SQLite queue → provider-specific admission/retry → VLM → Jev → finish/crop/review → JSONL. MVP: 100-image batch, concurrency limit, 429/5xx/interruption tests. Stack: asyncio, SQLite, Pillow, VLM/Jev. Resume value: backpressure/idempotency/recovery. A plain async map alone is not an Agent.

### 6. CaseFlow — multimodal after-sales workbench

Problem: track one case across text, images, order PDF, state and human review with CLI/API/MCP consumers. Flow: entry adapters → shared service → Agent/tool registry → file evidence/VLM/Jev/policy → SQLite → report. MVP: CLI, one image, one persisted case. Then PDF, FastAPI and MCP incrementally. Stack: Python, FastAPI, Typer, SQLite, MCP, PyMuPDF, VLM/Jev. Resume value: shared application boundary and lifecycle. Full platform exceeds one week; deliver one vertical slice.

## Comparison and recommendation

Scores are design judgments, not benchmark results. Difficulty uses 5=hard; other dimensions use 5=high.

| Direction | Difficulty | Technical depth | Agent character | Multimodal | VLM/Jev fit | Extensibility | One-week completion |
|---|---:|---:|---:|---:|---:|---:|---:|
| CaseLens | 3 | 4 | 5 | 4 | 5 | 4 | 4 |
| VisionDesk MCP | 3 | 4 | 4 | 3 | 4 | 5 | 4 |
| DocCheck | 4 | 4 | 4 | 5 | 5 | 4 | 3 |
| AssetLint | 2 | 3 | 3 | 4 | 4 | 4 | 5 |
| TriageBatch | 4 | 4 | 3 | 4 | 4 | 4 | 3 |
| CaseFlow | 5 | 5 | 5 | 5 | 5 | 5 | 2 |

Most reliable: AssetLint. Strongest Agent/multimodal interview story within a week: CaseLens. CaseLens is implemented here; the other five are proposals, not hidden feature claims.

### One-week CaseLens execution outline

| Day | Deliverable |
|---|---|
| 1 | Verify real API access, evidence schema, categories and policy |
| 2 | VLM → Jev decision, client limits and structured errors |
| 3 | Native tool calling, policy/crop/reinspection tools |
| 4 | Missing-information route, evidence gate, budgets and trace |
| 5 | Cache, retry/timeout/rate limit and reports |
| 6 | Labeled baseline comparison and separate error analysis |
| 7 | Clean install, regression cases, README and demo |

Use 20 development examples and an independently annotated 30-case holdout. Compare VLM-direct, VLM evidence→ordinary LLM, VLM evidence→Jev fixed workflow, and adaptive Agent. Report accuracy/Macro-F1, automatic coverage/error, review rate, follow-up success, P50/P95, usage/cost if available, and Brier score only with full probability vectors. Never claim small-sample calibration or fabricate gains.

Suggested future résumé wording after real-provider execution and a labeled evaluation: “Built an evidence-grounded VLM/Jev after-sales triage agent with native tool calling, bounded visual re-inspection, information requests, provenance, typed API recovery and separate visual/decision error evaluation.”
