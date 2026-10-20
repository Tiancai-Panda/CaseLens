# CaseLens LangGraph MCP Skills 与上下文记忆改造设计

日期：2026-10-12。目标项目：Ubuntu-24.04 中的 `/home/tcpanda/projects/caselens`。

## 实施状态（2026-10-12 更新）

设计已在 WSL 项目实施。当前流程使用 LangGraph 0.6.11，图片工具实际经 MCP SDK 1.30.0 stdio 子进程执行；5 类 Skill、上下文预算与工单内结构化记忆已接入 Web/CLI。

上次全量工程验证为 203 passed、Ruff/依赖检查/wheel 构建通过；模型使用 replay/mock，本地 MCP 为真实协议调用。Jev 当前不可连接，没有本轮真实 API 或模型效果验证。

实施差异：CaseStore 继续承担检查点，不使用 LangGraph 原生 checkpointer 或 interrupt；摘要是确定性记录，不是 LLM 摘要/RAG；图片 MCP 自动由 Agent 管理；不自动执行退款/发货。

最新运行说明与面试材料见 [项目指南](../../../PROJECT_GUIDE.zh-CN.md) 和 [简历与问答](../../../PROJECT_RESUME.zh-CN.md)，当前规格见 [design.md](../../design.md)。以下原始设计/调研保留，其中“当前/尚无”等描述属于改造前基线，不代表实施后的状态。

## 改造前目标与基线

本次改造将现有手写 Agent 循环迁移到 LangGraph，并增加真正的图片处理 MCP 服务、可加载的场景 Skills，以及受预算控制的上下文与工单记忆。现有 CLI、Web、SSE、视觉证据、Jev 决策、补证和人工复核保持可用。

代码核对结果：`agent.py` 当前以 for 循环执行控制器和工具；`customer.py` 用 allowed_tools 控制动作合法性；`cases.py` 用 SQLite 保存工单与检查点；`assets.py` 的裁剪结果只保存在进程内存。当前意图只有包装问题、商品问题、数量不足和错发，不包含独立退货诉求。WSL 虚拟环境尚无 pytest，原测试基线需要先建立。

## 技术选择

选择 LangGraph StateGraph 作为运行时。仅使用 LangChain 的抽象无法直接解决当前分支、循环和检查点的显式编排问题；同时引入两套 Agent 执行引擎会增加维护成本。本次不采用双引擎。

保留 VLMClient、JevClient、ControllerClient。LangGraph 承担执行编排；Controller 仍通过原生 Function Calling 选择工具；Jev 继续接收结构化状态，不转换为聊天模型。

依赖采用安装时实际可用且通过集成测试的已发布版本，由 uv.lock 锁定。MCP Python SDK 的 API 以该锁定版本为准，不假定未核验的版本或类名。

## 一 LangGraph 执行图

节点：bootstrap → load_skills → prepare_context → controller → tools → checkpoint → 条件路由。

- bootstrap：Jev 意图识别，或从同工单已保存状态恢复；单纯退货进入独立诉求。
- load_skills：根据诉求选择一份或多份受信任的本地 Skill，按需加载参考材料。
- prepare_context：创建满足预算的控制器请求，保留当前事实、缺证项、合法工具与剩余预算。
- controller：生成原生工具调用，校验调用数量和有效性。
- tools：执行本地业务工具和 MCP 图片工具；仅依赖无关的调用可并行。
- checkpoint：保存结构化工单记忆、关键证据及执行进度。
- finalize：证据充分时结束；需要用户材料时暂停本次运行；失败或预算耗尽时返回现有部分结果。

以 StateGraph 的条件边承担循环和退出，而不是在单个图节点中包裹旧的完整 for 循环。保留最大控制器轮数、补证上限、重复调用检测、状态门控与事件协议。Graph recursion_limit 只是额外保护，不替代业务预算。

持久化继续以 CaseStore 为工单权威来源；暂不建立另一套与 CaseStore 竞争的 LangGraph SQLite 工单存储。LangGraph 节点检查点写回 CaseStore。resume 的语义是基于持久化证据和记忆继续工单，不宣称可以从任意机器指令位置恢复。

## 二 图片 MCP 服务

新增 stdio MCP Server，提供 crop_image、resize_image、rotate_image。Agent 通过 MCP Client 进行 initialize、list_tools 和 call_tool，将发现的输入 Schema 映射给 Controller。调用实际经过协议，不只是给普通函数改名。

- 默认由 CLI/Web 自动启动受控子进程，不要求用户另开终端。
- 增加独立启动命令，便于外部 MCP Host 使用。
- 输入为服务可识别的 asset_id；外部 Host 先通过注册工具登记允许根目录内的图片，再使用返回 ID。
- 不接受工具参数中的任意文件读写路径；校验文件大小、像素数、裁剪边界、缩放尺寸和旋转角度。
- 对变换生成派生图片，保留原图，不覆盖用户材料。
- 派生图保存到工单目录，记录 root_asset_id、parent_asset_id、操作参数和变换链；旋转后的坐标不能被误报为原图坐标。
- MCP 输出结构化元数据，供 VLM 继续分析；图像变换本身不产生新的视觉事实。
- 服务断连或调用超时进入明确工具错误，不静默退回本地实现冒充 MCP 成功。
- 日志写 stderr，stdout 仅用于协议。子进程由异步生命周期管理，任务取消或 Web 关闭时清理连接和进程。

## 三 场景 Skills 框架

Skill 位于包内，可随 wheel 安装。每份采用 Agent Skills 规范的 SKILL.md（YAML frontmatter + Markdown），name 和 description 为标准元数据；manifest.json 仅为 CaseLens 自定义的业务扩展，记录意图映射、资源白名单与确定性工具绑定，不声称是开放规范必需项。按需包含 references、schemas、examples 和受信任辅助代码。

预置退货申请、货不对版、数量不足、包装残缺四种；商品损坏沿用现有路径，并提供对应证据规范。

Skill Registry 支持发现、字段校验、版本记录、意图路由和按需参考资料加载。Skill 规定分析步骤与证据要求；实际处理权限由 Policy 决定。Skill 内容不能跳过 Pydantic 校验、合法工具状态或人工审批。

复杂案例选择数量不足：

```text
missing-quantity/
  SKILL.md
  manifest.json
  references/evidence-checklist.md
  references/split-shipment.md
  schemas/quantity-evidence.schema.json
  examples/multi-package.json
  scripts/check_quantity.py
```

脚本是随项目发布的确定性核对逻辑，通过受控工具调用，不执行用户上传代码。核对订单应收数量、实收证据、分包待到数量和未知项；分包或证据不完整时请求补证，禁止仅凭用户自报数量认定少发。

退货诉求需区分用户希望退货与商品存在问题。示例规则只用于演示；不编造商家退货期限、金额、订单状态，也不自动创建真实退货单。

## 四 上下文与记忆

分为完整持久记录与有界模型上下文。SQLite 保存原始材料、证据、未知项、历史动作、摘要、摘要依据和版本；模型每轮只接收与当前问题相关的精简视图。

- 配置输入预算、输出预留和近期完整交互数量；预算包含系统提示、Skill、工具 Schema 和消息。
- 未知供应商 tokenizer 使用明确标注的保守估算；不宣称估算是实际 Token 数。必要内容超过预算时返回 context_budget_exceeded，不发送必然超限请求。
- 首先去掉重复的完整状态快照，再将旧交互整理为结构化滚动摘要；保留最近完整的 assistant tool_calls 与对应 tool 消息，禁止留下悬空调用。
- 摘要优先由确定性结构化记录生成，不额外依赖一个付费摘要模型；保留未解决问题、失败原因和证据引用，不把摘要当作新增证据。
- 关键证据保留用户当前诉求、来源 ID、已核实事实、冲突、缺证项、政策版本、人工约束和待办；大段 OCR 可只带摘录，需要时通过证据读取工具取回。
- 完整证据始终由业务决策层读取，上下文压缩不删除 CaseStore 中的事实或改变 Jev 的决策门槛。
- 同工单 resume 恢复摘要和证据；替换图片或纠正文字后使过期摘要、引用与旧判断失效。
- 记忆仅在当前工单内使用，不跨客户共享个人信息，不把缓存称作长期记忆。
- 已激活 Skill 的完整主说明置于受保护上下文，压缩时不删除；同版本重复激活不重复注入。历史按完整 assistant/tool 交互组裁剪，不照搬“只留最后两条消息”的教学示例。

## 五 用户可见变化

Web 仍使用当前入口，增加图节点、选中 Skill、MCP 操作与上下文压缩事件。结果中的 runtime、skills、context_stats、memory 等诊断字段为向后兼容的可选字段。CLI 保留现有命令，新增 MCP 服务启动及 Skill 列表/检查入口。旧 caselens.wsl.json 通过默认配置继续可用。

## 六 验收

1. 先安装开发依赖并运行原测试，确认已有失败项，再逐项补功能测试。
2. 正常、模糊、无关图片、数量不足、错发、多问题和补证恢复通过 LangGraph 运行。
3. MCP 通过真实 stdio 子进程完成发现、裁剪、缩放、旋转；非法参数、未知 ID、断连和超时有明确结果。
4. 原图与派生图有可追溯关系，派生图能跨进程恢复，坐标说明不失真。
5. 退货意图不被误写为商品损坏；复杂数量 Skill 正确处理分包待到、缺证和数量冲突。
6. 长历史触发摘要后请求不超过配置的估算预算；工具调用配对完整，关键证据和冲突仍可取回。
7. 同工单续办恢复记忆；更换材料使旧引用失效；不同工单相互隔离。
8. 现有 Web、CLI、SSE、报告、取消和恢复行为回归通过；Ruff 及 wheel 构建通过，Skill 资源包含在安装包中。
9. 用回放和模拟 API 验证编排，用真实 MCP 验证协议；真实供应商调用与业务准确率单独记录，不能用模拟成功替代效果评测。

## 实施顺序

开发环境与原测试基线 → LangGraph 迁移 → 派生图片持久化与 MCP 集成 → Skills 和退货流程 → 上下文摘要与工单记忆 → Web/CLI 展示及全量验证。

本次不增加真实退款/发货执行、多智能体、跨用户记忆、生产认证或分布式任务服务。

## 七 GitHub 与官方文档定向调研

调研日期：2026-10-12。本章回答四项改造应参考什么、参考到了哪一层，以及哪些结论会改变实现方式。

### 7.1 证据范围

本次尝试 GitHub API、raw 源码和网页读取。Windows 沙箱、外部终端与 WSL 网络均出现连接拒绝；浏览器成功读取部分仓库页面、LangGraph 核心源码以及官方文档，但一些后续源码页面仍连接重置。

因此采用三种证据等级：

- **本次源码核验**：浏览器读到了具体实现或源码说明，只解释实际可读范围。
- **本次官方文档核验**：读到了 README、规格或官方指南，不把文档示例描述为完整源码审计。
- **此前核验记录／本次未复核**：沿用项目 `docs/project-options.md` 中 2026-10-07 的研究记录，明确时间和限制。

没有安装、运行或执行任何参考项目。没有成功取得完整 release/tag 信息，因此本章不宣称某个版本是当前最新，也不使用 Star 数证明稳定性。LangGraph 有一份固定提交源码链接；其余 main 链接与官方指南可能随时间变化，实施前需要锁定发布版本并检查 API。

| 项目 | 本次读取范围 | 对 CaseLens 的结论 |
| --- | --- | --- |
| langchain-ai/langgraph | README；StateGraph 源码可读部分；官方 memory 和 add-memory 指南 | 采用 StateGraph，参考局部状态更新、条件路由及消息管理约束 |
| modelcontextprotocol/python-sdk | 官方 README 的服务器、客户端、安装和传输说明；Client 源码页访问失败 | 采用官方 SDK；类名、初始化和连接生命周期以锁定版本为准 |
| lastmile-ai/mcp-agent | 本次仓库访问失败；此前 Agent/aggregator 源码记录可用 | 参考命名空间、发现和生命周期，不引入整个框架 |
| huggingface/smolagents | 本次仓库访问失败；此前 agents/tools/models/mcp_client 源码记录可用 | 参考有界工具循环和校验，不增加第二套 Agent 引擎 |
| HKUDS/nanobot | 本次仓库访问失败；此前 loop/registry/mcp/cli 源码记录可用 | 参考 Registry 与异步错误处理，不复制个人助理平台 |
| agentskills/agentskills | GitHub 仓库访问失败；官方规格和客户端集成指南完整相关内容可读 | 以开放规范为 Skill 外部格式，自己实现受控 Registry |
| anthropics/skills | 本次仓库访问失败 | 保留示例候选，不宣称已读具体多文件 Skill 或许可全文 |
| langchain-ai/deepagents | GitHub 仓库访问失败；官方 overview 可读 | 参考渐进式 Skill、摘要与结果卸载；首版不引入其完整 Harness |

### 7.2 LangGraph 核心编排

仓库：[langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)。README 展示 MIT 许可，并说明 LangGraph 可独立于 LangChain 顶层库使用；这不等于依赖树完全没有 langchain-core。

本次源码链接：[StateGraph 固定提交](https://github.com/langchain-ai/langgraph/blob/799c2f04fd3a56186e24e9fb8bbf3568e543cfbe/libs/langgraph/langgraph/graph/state.py)。核验了 StateGraph 的类说明、add_node、add_edge 以及 add_conditional_edges 的可读声明/说明；GitHub 长源码视图截断部分文件，不声称已完整读完 compile 或底层 Pregel 引擎。

具体发现：

1. 节点的接口是 `State -> Partial<State>`，读共享状态、返回局部更新。Reducer 的形状是 `(Value, Value) -> Value`，用于聚合字段更新。
2. 多个起点通过 add_edge 合流时，文档说明会等待所有起点完成。条件路由可以返回单个或多个目的地，END 表示停止。
3. add_node 文档支持异步节点和节点 retry_policy；这不意味着应对付费模型再叠加一层重试。
4. README 支持 durable execution、human-in-the-loop 与 memory；这些能力仍需配置持久化和恢复流程，不能仅凭使用 StateGraph 就宣称全部具备。

应用决策：

- 将旧 for 循环拆为节点和条件边，新增的图状态至少包含 `round_number`、`phase`、`pending_calls`、`errors`、`context_stats` 与最终状态；业务证据用可序列化 ID/契约管理。
- 运行期 HTTP Client、MCP 连接、SQLite connection 和事件回调不放入持久图状态。通过受控运行上下文/依赖注入提供，避免序列化失败及密钥进入检查点。
- 对并行更新字段明确 Reducer；不能让两个节点无规则覆盖同一状态字段。
- Controller 决策轮数与图 super-step 不是同一指标，6 轮限制单独计数，recursion_limit 根据节点数留余量。
- Client 是模型请求重试唯一责任方；图节点层不对整段模型调用再次付费重放。
- 第一阶段继续 CaseStore 业务检查点，不宣称已实现 LangGraph 原生 checkpointer 或精确图节点断点恢复。后续若接入原生 checkpointer，需要明确唯一权威状态和数据库事务策略。

### 7.3 MCP Python SDK 与图片服务

仓库：[modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk)。本次 README 明确官方身份、MIT 许可和 Python 3.10+；CaseLens 当前 Python 3.11+ 与之兼容，但实际依赖兼容仍需安装验证。

本次 README 示例使用 `from mcp.server import MCPServer` 和 `from mcp import Client`。函数类型注解用于生成工具 Schema；Client 示例用异步上下文并读取 `result.structured_content`。README 说明支持 stdio、Streamable HTTP 和 SSE，并说明 Client 可以启动本地 stdio 子进程。没有成功读取本次 Client 实现，初始化、超时、工具缓存及进程关闭的具体方法必须按锁定版本补查。

应用决策：

- 只将 SDK 作为协议依赖，不引入一个新的规划器。
- 新增独立图片处理 Service，MCP Tool 装饰层负责协议转换；同一 Service 可被测试调用，但 Agent 的图片操作必须实际经过 MCP。
- 把 MCP tool 的 inputSchema 映射为 Controller Function Calling 参数；公共名称使用 `image_crop`、`image_resize`、`image_rotate` 或一致的命名空间，兼容旧 crop_image 别名时明确分发映射。
- 服务拥有持久 asset registry/manifest，外部 Host 注册工具只接受配置允许根目录下的相对文件引用，再返回 asset_id。源图内容哈希和派生图来源必须可跨进程核验。
- structured_content/isError 等输出字段通过版本适配层统一，不能把文本输出误判为结构化成功。
- SDK 若自动初始化，就不手动重复 initialize；若是底层 Session API，则明确执行握手。建立真实 stdio 集成测试验证生命周期。
- 启动图片子进程时不传模型密钥，只传允许的根目录和必要运行设置；不把 caselens.json 暴露给图片服务。
- 裁剪、缩放和旋转只改变输入图像，不新增“商品问题已核实”的事实。旋转可能改善文字方向，但放大不等于恢复模糊细节。

### 7.4 mcp-agent 的聚合边界

仓库：[lastmile-ai/mcp-agent](https://github.com/lastmile-ai/mcp-agent)。本次连接失败，下面源于 2026-10-07 记录，许可与维护快照不作为本次刷新结果。

此前源码入口：

- [agent.py](https://github.com/lastmile-ai/mcp-agent/blob/main/src/mcp_agent/agents/agent.py)：初始化、attach_llm、异步生命周期。
- [mcp_aggregator.py](https://github.com/lastmile-ai/mcp-agent/blob/main/src/mcp_agent/mcp/mcp_aggregator.py)：多服务器发现、server-qualified 名称与调用分发。

可借鉴：多个工具服务采用命名空间避免同名冲突；发现阶段用并发及异常结果隔离；关闭时清理连接。当前只有一个图片 Server，因此无需聚合整个框架，也不增加 Temporal、分布式工作流或多服务部署。以后增加订单/知识库 MCP 再扩展服务器映射。

### 7.5 smolagents 的工具循环与错误反馈

仓库：[huggingface/smolagents](https://github.com/huggingface/smolagents)。本次连接失败，沿用 2026-10-07 的源码核验记录。

入口：[agents.py](https://github.com/huggingface/smolagents/blob/main/src/smolagents/agents.py)、[tools.py](https://github.com/huggingface/smolagents/blob/main/src/smolagents/tools.py)、[models.py](https://github.com/huggingface/smolagents/blob/main/src/smolagents/models.py)、[mcp_client.py](https://github.com/huggingface/smolagents/blob/main/src/smolagents/mcp_client.py)。

此前发现：多步循环有步数预算、错误进入后续上下文、工具名/参数校验及最终结果检查；ToolCallingAgent 的多调用可用 ThreadPoolExecutor，不是原生 asyncio 的证据。

应用决策：保留 CaseLens 的工具校验、错误 Observation 与结束门控，迁移到 LangGraph 节点内；不复制任意 Python 执行能力，也不把 smolagents 与 LangGraph 并用为两个控制器。实际概率阈值、Policy、补证状态与审批规则是 CaseLens 自己的业务代码。

### 7.6 nanobot 的 Registry 和会话边界

仓库：[HKUDS/nanobot](https://github.com/HKUDS/nanobot)。本次连接失败，沿用 2026-10-07 记录。

入口：[loop.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/loop.py)、[registry.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/tools/registry.py)、[mcp.py](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/tools/mcp.py)。

此前发现：Registry 独立于 Loop，准备调用与异步执行分离，错误结果回传模型；MCP 适配处理超时、连接清理和图像内容。

应用决策：为 CaseLens 增加独立 SkillRegistry 与 MCPToolAdapter，避免 Agent 主文件承担文件扫描、协议管理与全部业务规则。协议图像内容不能被统一字符串化而丢失；本次图片操作可只返回来源与派生 asset 元数据，由 VLMClient 读取服务管理的图片。nanobot 的完整会话/聊天渠道/个人助理架构不进入依赖。

### 7.7 Agent Skills 格式与复杂场景包

仓库候选：[agentskills/agentskills](https://github.com/agentskills/agentskills)、[anthropics/skills](https://github.com/anthropics/skills)。两个 GitHub 仓库本次未能读取，未审核其参考实现或许可全文。成功核验的权威材料是 [Agent Skills 规格](https://agentskills.io/specification) 和 [客户端集成指南](https://agentskills.io/client-implementation/adding-skills-support)。

规格发现：

- Skill 最少包含 SKILL.md，文件是 YAML frontmatter 后接 Markdown；name 与 description 必需。
- name 长度 1–64，使用小写字母/数字/连字符，不以连字符开头/结尾，不含连续连字符，并匹配目录名。
- description 长度 1–1024，说明用途及触发条件。
- scripts、references、assets 为可选资源目录；schemas/examples/manifest 属于允许的附加内容，但不是规范强制结构。
- 渐进式披露为“目录元信息 → 激活时完整主说明 → 按需资源”。主文件建议少于 500 行，完整说明推荐小于 5000 tokens；这些是指导值，不是业务准确性保证。
- allowed-tools 是实验性字段，不等于运行时已经授权执行所有列出的工具。

客户端指南发现：支持启动时发现、目录元数据披露、完整激活、按需读取、激活去重和上下文压缩保护。指南列出多个扫描目录与兼容性宽松策略，但不要求 CaseLens 全部实现。

对原方案的修订：

1. 目录采用 `return-request`、`wrong-item`、`missing-quantity`、`damaged-packaging`、`product-issue`；数据库意图如 missing_quantity 保持兼容，通过 manifest 映射。
2. 每份 SKILL.md 有标准 frontmatter，manifest 记录 CaseLens 特有路由/资源与工具信息，两者校验一致。
3. 默认只发现包内受信任 Skill；首版不扫描用户主目录或任意上传路径。资源读取检查相对路径、软链接、文件数和大小。
4. 控制器看到目录；bootstrap 后按意图自动激活相关 Skill，允许受控 activate_skill/read_skill_resource 工具补充细节，不把所有参考文件预先塞进上下文。
5. 活跃 Skill 主说明始终受保护，不在摘要后丢失；资源加载去重并记录 hash/version。
6. allowed-tools 只作为候选声明；最终工具集由 Skill 白名单、当前状态合法工具和服务可用工具取交集。
7. 数量不足 Skill 脚本只运行固定受信任入口，不使用 eval、动态 shell 或任意脚本路径；脚本结果引用输入证据，不将用户主张升级为事实。

复杂数量案例：订单应收 3 件，当前照片 2 件，另有 1 件分包配送中的物流证据。确定性脚本返回“尚不能认定少发，待核对分包状态”，而不是简单用 3-2 推导补发 1 件。若只有用户描述而无订单或完整收货图，则返回缺证；脚本不是视觉计数模型。

### 7.8 LangGraph 与 Deep Agents 的上下文和记忆

成功核验：[Memory overview](https://docs.langchain.com/oss/python/concepts/memory)、[Add and manage memory](https://docs.langchain.com/oss/python/langgraph/add-memory)、[Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview)。deepagents GitHub 源码本次未成功读取，具体 middleware 行为不视为源码核验。

官方文档发现：

- 短期记忆属于 thread/state，持久化通常由 checkpointer 承担；长期记忆是跨会话、按 namespace 隔离的 store，两者有不同生命周期。
- 消息管理包括 trim、delete 与 summarize。trim_messages 接受 token_counter、max_tokens 和边界策略；count_tokens_approximately 明确是估算。
- 删除历史后必须仍满足供应商的消息有效性要求，尤其 assistant tool_calls 需要对应 tool 结果。
- 摘要示例扩展 MessagesState.summary；示例保留最近两条消息不保证适合多工具 CaseLens，必须按完整调用组调整。
- Deep Agents 文档支持 Skill 渐进披露、长结果卸载、摘要和可配置文件系统，并建立在 LangGraph 运行时之上。其完整执行/文件系统/子 Agent 能力明显超过当前需求。

应用决策：

- 不引入整个 deepagents、Mem0 或向量记忆服务。采用自己的 ContextManager 和工单记忆结构，利用 langchain-core 消息工具时先适配现有 OpenAI-compatible 字典契约。
- 仅变换模型视图；不会通过 RemoveMessage 删除业务证据数据库记录。完整历史与精简上下文分层保存。
- 单轮当前状态只注入一次；旧快照转为 action/error 摘要，避免 evidence 多次重复累积。
- 固定保留政策、活跃 Skill、最新诉求、关键证据 ID、缺证项、冲突与剩余预算。大 OCR/工具结果持久化后用摘要加读取引用，模型可按需获取完整内容。
- Token 预算覆盖 system/Skill/Schema/消息与输出预留。估算字段标记 estimator，不对未知供应商承诺精确计数；原始 usage 有值时分别记录。
- 每份摘要记录 `submission`、`evidence_ids`、`policy_version`、`skill_versions`、`summary_version` 与材料修订号。图像替换、文字纠正或规则版本变更触发失效/重建。
- Jev 仍读取完整受校验业务证据；面向 Controller 的摘要不成为新事实、不改变审批资格。
- 如果最小安全上下文超过预算，明确返回 context_budget_exceeded 并保留工单，而不是截断关键政策或无声漏掉证据。

### 7.9 最终技术决策与依赖边界

| 能力 | 采用 | 不作为本次依赖 | CaseLens 自己实现 |
| --- | --- | --- | --- |
| Agent 编排 | LangGraph StateGraph | smolagents/nanobot/deepagents 的完整运行时 | 售后节点、状态门控、补证路由、业务预算与事件 |
| MCP 图片协议 | 官方 MCP Python SDK 的已发布锁定版本 | mcp-agent 完整聚合框架 | 图片 Service、注册/派生 manifest、工具适配与生命周期 |
| Skill 格式 | Agent Skills 标准 SKILL.md | 其他 Agent 产品完整 Skill 引擎 | 业务 manifest、路由、受控读取与复杂数量核对 |
| 上下文管理 | 参考 LangGraph 官方消息规范；按需使用 langchain-core | deepagents 完整 Harness、向量长期记忆 | 预算、摘要、证据引用、受保护 Skill 与失效规则 |
| 工单记忆 | SQLite CaseStore | 新增竞争性的第二套工单数据库 | 同工单快照、摘要、材料版本与恢复 |

LangGraph、MCP、Skill 和 Memory 各自解决不同问题，不把它们当作互相替代的框架。图片工具是否可调用由状态和安全校验决定，Skill 提供任务程序性知识，Graph 决定步骤连接，ContextManager 决定每次模型看见什么。

### 7.10 调研后补充的验收项

在第六章验收基础上增加：

1. 图节点真实控制路由；测试证明不是单节点包裹旧循环。最大轮数按 Controller 调用计数。
2. 并行节点状态冲突有 Reducer 或避免并行写入；运行连接不进入序列化状态。
3. 相同 transient API 失败不会被 Client 与图节点重复重试放大。
4. MCP discovery Schema 与实际 crop/resize/rotate 调用相符，structured output 与错误通过实际 stdio 测试。
5. 图片子进程不继承模型密钥，关闭/取消后无遗留服务进程；未发现工具不假装成功。
6. Skill frontmatter 名称/目录/manifest 映射一致；资源路径越界、重复激活、非法脚本绑定有明确结果。
7. 上下文压缩后活跃 Skill 仍完整保留，assistant/tool 组配对完整；压力案例包含多工具一轮和大 OCR。
8. 关键材料改动使摘要引用失效；同工单恢复与其他工单隔离分别验证。
9. 安装 wheel 后标准 Skill 文件和附加资源可读取；依赖锁定后的真实 API/签名核对与回归结果写入 verification。

### 7.11 调研限制与实施前检查

本次已经完成可访问材料的定向研究，并对无法访问的候选说明限制；不是八个仓库的完整源码审计。尚未核验 MCP Client 本次完整源码、Anthropic 多文件示例、deepagents middleware、所有项目最新 release 和完整许可证。此前 smolagents/nanobot/mcp-agent 研究记录没有固定提交，因此只能作为历史模式参考。

实施前应在实际可联网环境下载发布依赖，记录版本与许可证，核对关键 API 并运行协议/图/消息最小集成测试。网络失败时不通过关闭 TLS 验证或未知镜像规避。参考设计不意味着复制源码；若以后复制源码片段，需要保留原许可/归属声明。
