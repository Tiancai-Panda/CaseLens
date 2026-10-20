# CaseLens 项目指南：功能、流程、运行与面试

更新日期：2026-10-12。以下内容对应 WSL Ubuntu 项目 `/home/tcpanda/projects/caselens`，不是 Windows 目录中的旧副本。工程验证使用离线 replay/mock 和本地 MCP；本轮没有调用真实 Jev、VLM 或 Controller API。

## 1. 项目做什么

CaseLens 是面向电商售后的多模态证据分诊 Agent。用户提交商品图片、问题描述和可选订单号，系统理解诉求、提取视觉证据、核查问题及缺证项，再提供补拍指导或政策约束下的处理建议。支持包装问题、商品问题、数量不足、货不对版、退货申请及复合诉求。

提供 Web 工作台和 CLI 两个入口。Web 用于上传、实时观察和调试；CLI 用于脚本、回放与工单查询。两者复用同一业务流程，不是两套独立 Agent。

系统不会退款、扣款、补发或修改订单。`--order-id` 只是订单上下文/关联编号，不会自动查询电商平台；演示可以使用测试编号。处理建议需要人工确认，现有演示政策不代表任何商家的正式政策。

## 2. 本次增加了什么

| 增加/改造 | 当前实现 | 解决的问题 |
|---|---|---|
| LangGraph | 用 StateGraph 节点和条件边替换手写主循环 | 显式编排分支、循环、退出和检查点 |
| 图片 MCP | stdio 子进程，发现并调用裁剪、缩放、旋转工具 | 将图片操作与业务 Service 分离并供外部 Host 复用 |
| 场景 Skills | 5 个包，包含说明、清单、Schema、示例及可信辅助脚本 | 把场景处理规范与通用运行时分开 |
| 上下文控制 | 预算、输出预留、完整调用组裁剪、结构化摘要、证据摘录 | 防止长历史无限进入 Controller 请求 |
| 工单记忆 | 同工单恢复动作、错误、缺证项和证据引用 | 补交材料时保留历史，而不重复开始 |
| 可观察性 | Graph 节点、Skill、MCP、上下文事件及诊断字段 | 展示当前阶段并支持定位问题 |
| 错误收口 | MCP 启动失败返回类型化结果，Controller 传输失败不重复跑图重试 | 避免异常冒泡和付费重试叠加 |

这些机制没有引入多 Agent 团队、通用代码执行沙箱或自动业务写入。

## 3. 依赖和模型分工

### 3.1 环境与库

Python 3.11+，当前验证环境为 WSL Ubuntu/Python 3.12。图片仅支持 PNG、JPEG、WEBP；不需要 GPU、本地部署、训练或微调模型。

| 技术 | 作用 |
|---|---|
| LangGraph 0.6.11 | Agent 执行图及条件路由 |
| MCP Python SDK 1.30.0 | stdio Server/Client、工具发现与调用 |
| Pydantic | 配置、工具参数、证据和结果校验 |
| PyYAML | Skill 主说明 frontmatter 解析 |
| Pillow | 图片读取与变换，不承担图像识别 |
| httpx / TypeSafe SDK | VLM/Controller HTTP 和真实 Jev 适配 |
| asyncio | API 等待、独立工具并发及取消 |
| Typer | CLI 命令及参数 |
| FastAPI / Uvicorn | 本地 Web API 和服务 |
| HTML/CSS/JavaScript / SSE | 工作台与实时事件，不需要独立前端构建 |
| SQLite（标准库） | 工单、证据缓存、历史与恢复状态 |
| pytest / pytest-asyncio / Ruff | 回归验证及静态检查 |
| uv / uv.lock | 环境安装与锁定版本 |

依赖以 `pyproject.toml` 和 `uv.lock` 为准。项目没有引入 Redis、Celery、向量数据库、独立 OCR 或 RAG 检索服务。使用 LangGraph，不是再搭建另一套 LangChain Agent 引擎。

### 3.2 三个逻辑模型角色

- **VLM**：图片 → 事实、文字、未知项。描述“右下角撕裂”，不直接决定“应退款”。
- **Controller**：读取当前状态和工具 Schema，通过原生 Function Calling 选择工具及参数。
- **Jev**：文字/结构化 state → 意图、问题核实、证据充分性、政策内建议。它不直接接收图片像素，也不是报告生成聊天模型。

VLM 服务若同时支持文字和原生工具调用，可以兼任 Controller。Jev 使用官方 TypeSafe SDK 的异步接口；模型 confidence 与候选类别概率分别保存，不能当作同一个指标。

## 4. 如何运行

### 4.1 进入已有 WSL 项目

在 Windows 终端进入 Ubuntu，然后：

```bash
cd /home/tcpanda/projects/caselens
.venv/bin/python --version
.venv/bin/caselens --help
```

不能复用 Windows 的 `.venv\Scripts`。Linux 使用 `.venv/bin`；本项目已迁移到 Linux 文件系统，不需要再次运行迁移脚本。

新机器首次安装（需要依赖下载网络，Python 3.11+、uv）：

```bash
cd /path/to/caselens
uv sync --locked --extra dev --no-editable --cache-dir .uv-cache
.venv/bin/caselens --help
```

修改源码后，如使用普通 wheel 安装，重新安装包：

```bash
uv sync --locked --extra dev --no-editable --cache-dir .uv-cache --reinstall-package caselens-agent
```

测试使用 `tests` 配置中的 `src` 源码路径；不能仅凭测试通过就断言旧安装包已更新。正常运行应使用更新后的环境。

### 4.2 Jev 不可连接时：先离线运行

```bash
.venv/bin/caselens demo --scenario clear
.venv/bin/caselens demo --scenario blurred
.venv/bin/caselens demo --scenario split
.venv/bin/caselens demo --scenario return
.venv/bin/caselens skills
.venv/bin/caselens skills --name missing-quantity
```

`demo` 自动生成合成图片，保存 `outputs/demo/<场景>/result.json` 和 `report.md`。无需模型密钥或付费 API，但图片操作仍走真实本地 MCP 子进程。离线模式不能检验图片识别准确率。

| 场景名 | 用途 |
|---|---|
| clear | 清晰包装证据，完成建议 |
| blurred | 模糊证据，裁剪重看 |
| missing | 缺订单信息，要求补充 |
| irrelevant | 无关图片，提示重新拍摄 |
| quantity | 数量不足路径 |
| multiple | 多个诉求 |
| return | 退货诉求，与损坏区分，转人工确认 |
| wrong | 货不对版核查 |
| split | 分包仍待到，避免直接认定少发并建议补发 |

也可使用自己的有效图片走回放：

```bash
.venv/bin/caselens inspect ./test.jpg \
  --text "外包装有破损，请帮我核实" --order-id DEMO-001 \
  --replay blurred --state-dir .caselens-offline \
  --output outputs/offline-case --format user
```

此时模型回答来自场景固定数据，并非分析上传图片得出的真实结论。

### 4.3 Web 启动与操作

已有 `caselens.wsl.json` 时显式指定它：

```bash
cd /home/tcpanda/projects/caselens
CASELENS_WEB_CONFIG=./caselens.wsl.json \
  .venv/bin/python -m uvicorn caselens.web:app \
  --host 127.0.0.1 --port 8765
```

如果实际使用 `caselens.json`，把上述配置路径替换为它；未指定时默认读取 `caselens.json`。状态目录来自该配置的 `state_dir`。Web 和 CLI 必须使用相同配置或同一状态目录，才能看到同一组工单。

Windows 浏览器打开 <http://127.0.0.1:8765/>。端口占用时改为 8766，并访问相应地址。无需 Node、npm 或前端打包；Node/Playwright 只用于浏览器自动化测试。

操作顺序：

1. 上传有效图片，输入问题描述与订单号。
2. Jev 暂不可用时选择录制回放场景；不要选择真实 API 模式。
3. 提交工单，在时间线查看 Graph 节点和工具执行。
4. 查看用户提示、决策、证据、Skills、上下文诊断和 JSON。
5. 需要补证时，在同一工单追加/替换图片或更正文字，再次提交。
6. 查看历史记录并下载 Markdown/JSON 报告。

浏览器不接触模型密钥。修改配置后重启服务，`Ctrl+C` 停止。默认没有公网认证或多租户隔离，不要改成公网监听。

### 4.4 真实 API 模式（待 Jev 恢复后）

从 `examples/caselens.example.json` 创建本地配置，填写供应商实际地址、模型名和 `api_key`。VLM 必须支持图片；Controller 必须支持原生工具调用。密钥也可用 `api_key_env` 指定的环境变量；该字段填变量名，不填 sk 值。

```bash
.venv/bin/caselens doctor --config ./caselens.wsl.json
# 以下命令会真实调用服务并消耗配额，Jev 未恢复时不要执行：
.venv/bin/caselens doctor --config ./caselens.wsl.json --probe
.venv/bin/caselens inspect ./test.jpg \
  --text "收到商品数量不够，请核实" --order-id ORDER-001 \
  --config ./caselens.wsl.json --output outputs/live-case
```

不带 `--probe` 的 doctor 只检查本地配置，不代表 API 可调用。真实配置不要提交、截图或写入项目文档。

上下文配置可以追加到 JSON 顶层：

```json
{
  "context_max_input_tokens": 24000,
  "context_output_reserve": 1200,
  "context_recent_groups": 3
}
```

这里只展示三个字段，不是完整真实 API 配置。名称虽含 tokens，当前采用保守 UTF-8 字节上界估算，不是供应商 tokenizer 的精确计数。

### 4.5 工单续办和查询

以下 `case_实际ID` 应替换为结果中的 `case_id`，不是 `run_id`：

```bash
.venv/bin/caselens resume case_实际ID --image ./new-photo.jpg \
  --text "已补拍标签" --config ./caselens.wsl.json --replay clear
.venv/bin/caselens case case_实际ID --config ./caselens.wsl.json --history
```

离线工单若创建时使用 `--state-dir .caselens-offline`，续办/查询也需传同一个 `--state-dir`。不使用回放时移除 `--replay`。替换图片使用 `--replace-asset <旧asset_id>`，更正而非追加文字使用 `--replace-text`。

### 4.6 测试与 MCP 独立运行

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/caselens tests
.venv/bin/python -m pip check
.venv/bin/python -m pytest tests/test_image_mcp.py tests/test_skills.py -q
```

Agent 自动启动/关闭图片 MCP，不必再开服务。外部 MCP Host 使用 stdio 命令：

```bash
.venv/bin/caselens mcp-server --root /path/to/allowed/images
```

该进程等待 MCP 协议输入，不是交互聊天终端；Host 先调用 `register_image(relative_path)` 得到 asset ID，再执行变换。日志在 stderr，stdout 只能输出协议，不要向其中打印调试文本。外部 Host 集成仍需配置并实测，不应声称所有 Host 都已兼容。

## 5. 完整执行流程与每部分作用

```text
Web / CLI → CaseStore 保存材料与工单
          ↓
启动图片 MCP → initialize / list_tools / 同步图片注册
          ↓
bootstrap → Jev 识别用户诉求或恢复工单状态
          ↓
load_skills → 按诉求激活场景说明
          ↓
prepare_context → 当前状态 + Skill + 记忆 + 最近完整调用组
          ↓
controller → 原生 Function Calling 选择动作
          ↓
tools → 参数校验 + 状态门控 → 本地业务工具 / MCP 图片工具
          ↓
checkpoint → 保存动作、错误、证据引用与工单记忆
          ├── 未结束且预算足够 → prepare_context
          └── 完成 / 补证 / 人工复核 / 失败 / 达预算 → finalize
```

LangGraph 负责节点顺序和条件边；Controller 决定可用工具中下一步做什么。Jev 做结构化业务判断。框架不会自动替你提供这两种模型能力。

### 5.1 输入、意图和 Skill

图片计算 hash，记录来源、尺寸、父图/根图 ID。用户诉求是“需要核实的问题”，不等于已经证实的事实。退货申请并不自动意味着商品损坏。

`load_skills` 选择一份或多份说明；工具可以按需读取参考资源。Skill 是分析规范，Policy 是业务权限与约束，两者不能混为一谈。

### 5.2 工具与证据链

| 工具 | 作用 |
|---|---|
| inspect_asset | 查看图片元数据 |
| analyze_visual | 调用 VLM 提取事实、文字、未知项 |
| crop_image / resize_image / rotate_image | 经 MCP 生成派生图，不覆盖原图 |
| read_policy | 读取当前售后政策 |
| activate_skill / read_skill_resource | 激活说明及白名单参考文件 |
| check_quantity | 执行可信数量核对逻辑，返回少发/分包/冲突/缺证状态 |
| read_evidence | 分页读取完整事实/OCR/未知项 |
| evaluate_issues / evaluate_decision | 客户流程问题核查 / 基础流程决策 |
| request_information | 输出具体补交要求，结束本次运行等待用户 |
| finish / escalate | 通过门控生成建议或转人工 |

仅相互独立的规则读取、图片检查或视觉分析可并行；依赖上一次派生图、评估或终态的工具必须分轮。MCP 变换只是像素处理，不会增加“图片中有什么”的事实；派生图需再经 VLM 分析。

### 5.3 复杂 Skill：数量不足

```text
skill_bundles/missing-quantity/
  SKILL.md
  manifest.json
  references/evidence-checklist.md
  references/split-shipment.md
  schemas/quantity-evidence.schema.json
  examples/multi-package.json
  scripts/check_quantity.py
```

主说明解释步骤；清单定义应补材料；Schema 定义数据；示例解释分包；脚本做确定性算术核对。脚本接收应收、实收、待到数量及证据标识，区分 `shortage`、`pending_shipment`、`consistent`、`conflict` 和 `missing_evidence`。

例如订单 5 件、实收 3 件、另一包待到 2 件，不应直接输出少发 2 件并补发。待到数量未知时也不能当作 0。工具校验数据类型及当前证据 ID；ID 存在不代表订单文字或计数一定正确，仍需视觉/业务核查。

这里只执行固定随包发布的可信脚本，不让模型指定任意脚本路径，也不执行上传代码。Skill 的 `manifest.json` 是项目扩展，不是开放规范要求。

### 5.4 上下文与记忆

完整记录保存在 SQLite/文件中，模型上下文只是有界投影：

1. 保留系统约束、已激活 Skill、工具 Schema、当前问题、证据引用和未解决项。
2. 长事实/OCR只提供摘录；Controller 可用 `read_evidence` 分页读取。
3. 旧动作和错误形成确定性结构化摘要，不额外调用摘要模型。
4. 最近 assistant tool_calls 和对应 tool 结果整组保留；超预算时删除最旧完整组。
5. 保护内容仍超限时返回 `context_budget_exceeded`，不继续发超限请求。
6. checkpoint 保存记忆；续办累计历史摘要，更换图片/更正文字使旧判断或摘要失效。

这是工单内持久记忆，不是跨用户长期画像、RAG 或向量检索。也没有使用 LangGraph 原生 checkpointer 来提供任意节点的精确恢复；CaseStore 是权威持久来源，resume 重新进入图并根据保存状态继续。

### 5.5 人机协同和失败

证据不足时提示重新拍摄完整商品、标签、破损处或补订单明细。补证以“本次运行结束 + 用户提交后续办”实现，不是挂着一个模型请求等待，也不是 LangGraph interrupt 审批节点。

最多 6 轮 Controller 决策、每个原图最多 2 次追加分析，并有重复调用检测。SDK 与 Client 不叠加重试；传输失败耗尽 Client 重试后停止，非法工具调用才可在有限轮内修复。MCP 启动失败返回 `mcp_unavailable`，不会静默降级成本地函数并冒充协议成功。

## 6. 如何看结果与调试

| 字段 | 来源/含义 |
|---|---|
| mode / status | replay 或 live；完成、待复核、部分结果或失败 |
| intent / findings / recommendations | Jev 诉求、核查和政策内建议；回放模式为固定测试响应 |
| evidence | VLM 输出及图片来源；回放模式为录制观察 |
| steps | 应用记录的工具名、参数、结果、轮次 |
| calls / latency_ms / errors | Client 调用追踪与应用运行记录 |
| runtime | 本轮编排器，当前为 langgraph |
| skills | 已激活场景及版本 |
| context_stats | 上下文估算、预算、保留/摘要组数 |
| memory | 结构化历史、证据引用、未解决项，不是隐藏思维链 |
| user_message / requested_information | 应用组织的建议与用户下一步操作提示 |

SSE 展示执行阶段和短事件，不显示模型隐藏思维链。看到合法类型或高概率不代表视觉事实正确。JSON 调试及报告可能含用户资料，演示需脱敏。

## 7. 代码阅读地图

| 文件 | 职责 |
|---|---|
| agent.py | StateGraph 节点、条件边、MCP 生命周期与运行收口 |
| customer.py / tools.py | 客户业务门控、通用工具注册及参数校验 |
| image_mcp.py / image_server.py / image_service.py | 协议 Client、Server 和独立图片变换服务 |
| skills.py / skill_bundles/ | Skill 加载、安全约束、场景资源 |
| context.py | 输入投影、估算、摘要和完整交互组控制 |
| cases.py / store.py | 工单、历史、检查点和缓存 |
| clients.py / resilience.py | 模型适配、重试、限流和熔断 |
| web.py / static/ / cli.py | 双入口及 SSE 页面 |
| report.py / schemas.py / replay.py | 报告、数据契约与离线场景 |

文件均位于 `src/caselens/`。完整简历表述及面试问答见 [PROJECT_RESUME.zh-CN.md](PROJECT_RESUME.zh-CN.md)。

## 8. 验证范围、拓展与安全边界

2026-10-12 上一轮全量验证为 `203 passed`，Ruff、pip check、wheel 构建通过。图片操作使用真实本地 MCP 子进程；模型使用 replay/mock，**未证明真实模型准确率或收益**。本轮文档更新的实际复核结果见 [docs/verification.md](docs/verification.md)。

优先拓展：真实 API 恢复后的端到端验证、固定脱敏标注集、审批节点、商家规则版本、订单只读查询。后续再考虑 LangGraph 原生 checkpointer、授权受控业务写入、多 Host MCP 测试、OpenTelemetry、认证/权限和部署。PDF/Office、向量记忆、多租户、分布式队列目前没有实现，不写进已完成简历。

模型能力依赖供应商，Skill 不能覆盖政策，图像变换不能凭空恢复细节，摘要不能作为新证据。不自动执行退款/发货，不公开密钥，不公网开放当前工作台。

