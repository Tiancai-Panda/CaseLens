# CaseLens：LangGraph 多模态售后 Agent

更新日期：2026-10-12。当前代码位于 WSL Ubuntu 的 `/home/tcpanda/projects/caselens`。Windows 项目副本未同步本次改造。

输入商品图片、用户描述和订单号，Agent 识别诉求、提取视觉事实、核查缺证项，给出补拍提示或政策内退款/补偿/补发/人工复核建议。只做辅助建议，不执行退款或发货。

- [完整项目指南：功能、依赖、流程与操作](PROJECT_GUIDE.zh-CN.md)
- [简历分点与 HR/技术面试问答](PROJECT_RESUME.zh-CN.md)
- [当前实现规格](docs/design.md)
- [设计及 GitHub 调研](docs/superpowers/specs/2026-10-12-runtime-design.md)
- [验证记录](docs/verification.md)

## 本次新增

- **LangGraph StateGraph**：bootstrap、load_skills、prepare_context、controller、tools、checkpoint、finalize 节点和条件循环。
- **真实 stdio MCP**：裁剪、缩放、旋转工具发现/调用，原图保护、派生图持久化和来源追踪。
- **5 类 Skills**：退货、货不对版、数量不足、包装残缺、商品问题；复杂数量 Skill 包含清单、Schema、分包示例及可信脚本。
- **上下文与工单记忆**：输入预算、输出预留、结构化摘要、最近完整调用组、关键证据和分页读取。
- **调试展示**：SSE 图节点、Skills、MCP、上下文事件和结果诊断；保留原 Web/CLI 及工单续办。

Jev 负责结构化决策，不直接看图。Controller 通过原生 Function Calling 选择工具；LangGraph 负责编排，不是额外聊天模型。Agent 自动管理 MCP 子进程，不必手动先启动服务器。

## WSL 快速运行（当前推荐离线）

```bash
cd /home/tcpanda/projects/caselens
.venv/bin/caselens --help
.venv/bin/caselens demo --scenario clear
.venv/bin/caselens demo --scenario blurred
.venv/bin/caselens demo --scenario split
.venv/bin/caselens demo --scenario return
.venv/bin/caselens skills
.venv/bin/caselens skills --name missing-quantity
```

报告保存为 `outputs/demo/<场景>/result.json`、`report.md`。demo 无需 API，自动生成合成图。所有回放场景：clear、blurred、missing、irrelevant、quantity、multiple、return、wrong、split。

**Jev 当前不可连接时不要运行真实模式或 doctor --probe。** 回放响应为录制/固定数据，只验证流程，不验证所上传照片的真实识别能力。

自己的有效图片也可走回放：

```bash
.venv/bin/caselens inspect ./test.jpg \
  --text "包装破损，请核实" --order-id DEMO-001 \
  --replay blurred --state-dir .caselens-offline \
  --output outputs/offline-case --format user
```

## 启动 Web

```bash
cd /home/tcpanda/projects/caselens
CASELENS_WEB_CONFIG=./caselens.wsl.json \
  .venv/bin/python -m uvicorn caselens.web:app --host 127.0.0.1 --port 8765
```

如使用 `caselens.json`，替换配置路径；默认读取 `caselens.json`。Windows 浏览器打开 <http://127.0.0.1:8765/>，选择回放场景、上传图片、填写问题并提交。查看时间线、建议、证据、历史和 JSON；需要补证时继续同一工单。

状态目录取自配置，CLI/Web 使用同一目录才能共享工单。修改配置需重启；端口占用则改用 8766；`Ctrl+C` 停止。无需 Node/npm 构建前端。浏览器测试另需 Node/Playwright。工作台没有公网认证，保持仅本机访问。

## 安装或更新

Python 3.11+ 和 uv；在项目目录执行：

```bash
uv sync --locked --extra dev --no-editable --cache-dir .uv-cache
# 修改源码后更新普通安装包：
uv sync --locked --extra dev --no-editable --cache-dir .uv-cache --reinstall-package caselens-agent
```

当前 WSL 已有 Linux `.venv`，不复用 Windows 的 `.venv\Scripts`。依赖通过 `uv.lock` 固定，其中 LangGraph 为 0.6.11、MCP SDK 为 1.30.0；不要混用早期未核验的 MCP 2.x 示例。

## 真实 API 配置（Jev 恢复后）

参考 `examples/caselens.example.json` 填写 VLM/Controller 的实际 base_url、model、api_key 和 Jev 凭据。非空 api_key 优先，否则读取 api_key_env 命名的环境变量。支持图片与原生工具调用的同一服务可兼任 VLM/Controller。不上传真实配置或密钥。

```bash
.venv/bin/caselens doctor --config ./caselens.wsl.json
# 会真实调用服务和消耗配额：
.venv/bin/caselens doctor --config ./caselens.wsl.json --probe
.venv/bin/caselens inspect ./test.jpg --text "收到的商品有问题" \
  --order-id ORDER-001 --config ./caselens.wsl.json --output outputs/live-case
```

不带 probe 的 doctor 只查配置，不能证明模型可调用。VLM structured_mode 支持 json_schema/json_object，以供应商能力为准；不要仅通过改 Prompt 处理错误端点、鉴权或模型名。

## 工单恢复与新命令

```bash
.venv/bin/caselens resume case_实际ID --image ./new-photo.jpg \
  --text "已补拍标签" --config ./caselens.wsl.json --replay clear
.venv/bin/caselens case case_实际ID --config ./caselens.wsl.json --history
# 外部 MCP Host 的 stdio 服务（Agent 自己运行时无需手动执行）：
.venv/bin/caselens mcp-server --root /path/to/allowed/images
```

resume 使用 case_id，不是 run_id。外部 Host 先注册根目录内图片，再用 asset_id 调用变换。skills 命令及 Web `GET /api/skills` 不调用模型。

analyze 是单次视觉提取；classify 是固定 VLM→Jev 流程；inspect 是动态客户 Agent。batch 支持受限并发与恢复；chat 支持同工单补证；evaluate 汇总已有标注数据，不生成实验成绩。

## 运行与结果边界

最多 6 轮决策、每原图最多 2 次追加分析，工具 Schema 和合法状态门控在代码层执行。新材料使旧判断失效；重试在 Client 层，不与 SDK/图循环叠加。MCP 启动失败返回 mcp_unavailable；保护上下文过大返回 context_budget_exceeded。

结果新增 runtime、skills、context_stats、memory。记忆为同工单结构化历史，不是向量库或跨用户画像。完整证据保留，模型只带摘录；SSE 不展示隐藏思维链。

JSON stdout 与 stderr 日志分开。退出码：0 完成/待复核，1 执行失败，2 参数/配置错误，3 批量部分失败。终端 JSON 转义中文以兼容旧 PowerShell 解码；保存的报告使用 UTF-8。

## 测试与尚未完成的内容

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src/caselens tests
.venv/bin/python -m pip check
```

上次完整工程验证 203 项通过，Ruff/依赖检查/wheel 构建通过；模型用 replay/mock，MCP 使用真实本地协议。当前没有真实标注集准确率、收益或成本下降结论。浏览器回归另需安装 Node/Playwright，不能把已有记录当作本轮重跑结果。

未实现 PDF/Office、RAG、跨工单长期记忆、LangGraph interrupt 审批、多租户或真实订单写入。原六方案与一周路线是设计背景，不代表全部已交付。详细验证边界见 docs/verification.md。
