# 交付验证记录

历史验证日期：2026-10-07；Windows，Python 3.12.3，中文工作区路径。当前 WSL 版本以本页后续 2026-10-12 记录为准。

| 验证 | 结果 |
|---|---|
| `python -m pytest -q` | 115 passed |
| `ruff check .` | All checks passed |
| `uv build --wheel --offline --cache-dir .uv-cache` | 构建 wheel 成功 |
| 新建 `.verify-venv` 后安装 wheel 及声明依赖 | 成功；导入位置为该环境的 site-packages |
| 独立环境中 `caselens --help` | 成功，中文帮助可用 |
| 安装后的 clear demo | replay / completed，4 个工具步骤，5 次录制调用 |
| 安装后的 blurred demo | replay / completed，包含 crop_image，7 个工具步骤，10 次录制调用 |
| 安装后的 missing demo | replay / needs_review，4 个工具步骤，5 次录制调用 |
| 示例配置的 doctor | 配置可解析；三个指定密钥变量均未设置 |
| 独立代码审查与修复复审 | 已处理异常消息、共享熔断设置、回放缓存、评测输入与调用记录累积问题 |
| 面向用户 Agent 回归 | 图片＋文字意图、无关照片指导、数量不足、复合诉求、同工单补交／替换、文本续办、并发 claim、取消后检查点、建议不执行 | 

回归测试验证了不同回放场景不串用视觉观察，部分缓存预热不把全图观察错配到裁剪图。真实推理模式继续使用版本化持久证据缓存。API 传输测试使用 httpx/httpx2 MockTransport；不是供应商线上调用。

`uv` 默认全局缓存在沙箱内不可写时可显式指定项目缓存，例如 `uv --cache-dir .uv-cache run --no-sync caselens --help`。中文路径使用非 editable 安装，避免 `.pth` 编码问题。

尚未验证：真实 VLM／Jev／控制器 API、真实图像判断质量、50 个独立标注案例及四类基线效果。没有准确率提升、校准或成本优化的实测结论。当时首版尚不包含 MCP、PDF／Office 或 HTTP 工作台。当前已加入 Web 与图片 MCP，PDF／Office 仍未实现，也不自动执行退款。

## 直接 JSON 密钥支持更新

按用户要求新增 `api_key`：非空时优先使用，空值回退到原环境变量。密钥排除配置序列化、repr、报告和批量指纹；环境端点覆盖保留密钥。共享供应商限流按实际凭据判断。

- 最新完整验证：91 tests passed；Ruff 全部通过。
- 最新完整验证：115 tests passed；Ruff 全部通过。
- 重新构建 wheel／sdist，并核验都不包含实际 caselens.json。
- `.venv` 和 `.verify-venv` 已安装新版；两者 doctor 均确认三个服务的密钥来自配置，输出不含密钥值。
- 真实 API 调用未执行；doctor 验证只代表配置读取成功。

当前版本新增的客户流程代码已在录制回放和模拟 SDK/HTTP 下验证；未宣称真实用户意图、问题核实或退款建议的准确率。真实处理建议仍须接入商家政策、订单系统和人工审批。

## Web 工作台更新

- FastAPI + Uvicorn + vanilla HTML/CSS/JavaScript 已加入，未引入 React 或独立前端构建链。
- API 测试覆盖健康检查、JSON Base64 图片上传、后台任务事件、工单查询、补证恢复、非法图片和密钥不泄露。
- Agent 事件只暴露运行开始、轮次、工具名、成功状态和最终状态；不暴露 Prompt、原始工具参数、图片内容或密钥。
- Web 任务在单独事件循环线程内运行；两个后台任务共享同一 Runtime 和供应商限流，关闭服务时取消任务、保存检查点并关闭客户端。
- 2026-10-08 最终验证：130 项 pytest 通过，Ruff 通过，Node JavaScript 语法检查通过。
- Playwright（本机 Edge）实测 1440px 桌面与 390px 手机：上传、创建、SSE、同工单补交、两次历史、证据图片加载通过，未出现页面脚本错误或横向溢出。截图保存在 outputs/web-desktop.png、web-result.png、web-evidence.png、web-mobile.png。
- API 验证覆盖错误配置脱敏、跨源拒绝、非法图像、后台取消、共享 Runtime 和关闭生命周期；新增 Web 工作流没有调用真实付费模型。
- 进程内任务及事件只保留最近 50 项，重启后可从持久化工单查询材料／结果；本机工具不具备公网身份认证和多用户隔离。

## 2026-10-12 WSL 运行时改造验证

本次验证在 WSL Ubuntu 项目 `/home/tcpanda/projects/caselens` 中执行，未修改 Windows 项目。测试使用离线 replay、mock Controller/VLM 和本地 stdio MCP；由于 Jev API 当前不可连接，没有发起真实 Jev、VLM 或 Controller 请求。

| 验证 | 结果 |
|---|---|
| LangGraph 运行时回归 | `203 passed`；包含 Graph 节点事件、预算、错误恢复、resume memory 和 MCP 启动失败契约 |
| MCP 图片服务 | 18 项独立测试通过；覆盖 discovery、裁剪、缩放、旋转、派生图恢复、路径防护和结构化结果 |
| Skills | 5 个场景 Skill 及资源/数量校验测试通过；包含多文件数量不足 Skill |
| Ruff | `All checks passed` |
| 依赖检查 | `No broken requirements found` |
| wheel | `caselens_agent-0.1.0-py3-none-any.whl` 构建成功，并核验 Skill 资源随包分发、密钥配置未进入包 |
| 浏览器工作台 | 既有 Playwright 桌面/移动端回归已通过；本轮 WSL 未重跑，因 WSL 无 Node 且 Windows 项目未安装 Playwright |

当前 LangGraph 版本为 `0.6.11`，MCP Python SDK 为 `1.30.0`。测试输出中的唯一 warning 来自 LangGraph 上游 `allowed_objects` 默认值即将变化，不是项目失败。

### 本次运行时边界

- Controller 传输错误不会被 Graph 无意义重试；只有非法工具调用进入有限修复轮。
- MCP 子进程启动失败返回 `mcp_unavailable` 结构化错误，并跳过 Controller 与工具步骤。
- 上下文按输入预算、输出预留、最近调用组和关键 evidence IDs 截断；完整证据仍保存在工单存储中。
- Skill 资源经过路径、大小、版本和 evidence ID 校验；数量检查脚本固定绑定到可信 Skill 包资源。
- 系统只给出退款、补发、补偿或人工复核建议，不自动执行这些业务操作。
- 本轮结果证明工程运行时契约和离线流程可回归，不代表真实模型准确率、校准度或业务策略有效性。


## 2026-10-12 文档同步复核

本次仅更新 WSL 项目的 Markdown 文档，不改业务代码，不同步 Windows 项目。README、项目指南、简历/面试说明、当前规格、设计/调研状态与实施记录共 11 份已同步。

- 文档检查通过：11 份 UTF-8 Markdown、代码块闭合、24 个项目内链接、20 组面试问答及复杂数量 Skill 的 7 个资源路径。
- 实际 CLI --help 验证：skills、mcp-server 及原有命令可见。
- 文档演示命令实跑：clear/blurred 为 completed；split/return 为 needs_review；全部 mode=replay、runtime=langgraph，报告生成成功。
- skills --name missing-quantity 实跑成功。
- 本轮重新运行原离线测试集：203 passed，1 warning，33.57 秒；warning 为前述 LangGraph 上游提醒。
- 没有执行 doctor --probe，也没有调用真实 Jev/VLM/Controller。没有新增真实效果或线上准确率结论。
- 本轮不重新启动浏览器回归或重建 wheel；原构建和浏览器限制保留为历史记录。安装命令为使用说明，没有在本轮重新下载依赖。

运行总览见 ../PROJECT_GUIDE.zh-CN.md；简历分点及 HR/技术面试问答见 ../PROJECT_RESUME.zh-CN.md。
