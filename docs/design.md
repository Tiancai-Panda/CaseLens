# CaseLens 当前实现规格



原改造设计与 GitHub 调研保留在 [runtime-design](superpowers/specs/2026-10-12-runtime-design.md)。设计已实施，最终版本、验证和落地差异以本页及 [verification](verification.md) 为准。

## 架构与模型

Web/CLI → Case Service → LangGraph Agent → ToolRegistry → 模型 Client / stdio 图片 MCP / Skills / 证据及政策 → CaseStore/报告。

VLM 提取事实、文字和未知项；Jev 接收结构化状态进行意图、核查、充分性与建议决策，不看图片像素；Controller 通过原生 Function Calling 选择下一步工具。真实 Jev 适配保留 TypeSafe 异步 SDK，图编排不取代模型能力。

## 执行图

```text
START → bootstrap → load_skills → prepare_context → controller
                                          ↑             ↓
                                     checkpoint ← tools
                                          ↓
                            继续循环 / finalize → END
```

bootstrap 支持意图识别和工单恢复；Skill 按诉求加载；上下文投影后 Controller 选工具；执行结果写回后保存检查点。完成、补证、失败及达到预算经条件边结束。

最多 6 轮决策、每原图最多 2 次追加分析，并校验最大调用数、重复调用和动作依赖。仅独立 inspect_asset/read_policy/analyze_visual 调用可同轮并行。Graph recursion_limit 是额外保护，不替代业务预算。

## MCP 图片能力

固定 MCP SDK 1.30.0，使用真实 stdio 子进程完成 initialize、list_tools、register_assets、call_tool。将发现的三类图片工具映射给 Controller：

- crop_image(asset_id, region)：归一化区域，校验有效面积。
- resize_image(asset_id, width, height)：正整数尺寸及像素限制。
- rotate_image(asset_id, angle)：有限值、范围 [-360, 360]。

派生图保存在根目录内 .image-tools，记录 root/parent ID、hash、transforms 和实际像素路径；不覆盖原图。旋转后不误报为原图坐标。服务限制路径和 symlink、注册后内容变化、格式、大小和像素数。

Agent 自动管理连接；外部 Host 可独立启动 mcp-server 并先 register_image。服务不读取模型密钥，不静默 fallback。启动失败返回 mcp_unavailable。多独立 Host 共享同一根目录的跨进程写锁与全面兼容测试未实现。

## Skills

5 类：return-request、wrong-item、missing-quantity、damaged-packaging、product-issue。包内 SKILL.md 配标准 name/description，项目 manifest.json 定义版本、意图与资源白名单。

数量 Skill 包含 references、Schema、example 和固定可信 check_quantity.py，区分 shortage/pending_shipment/consistent/conflict/missing_evidence。不执行上传或由模型指定路径的代码，Skill 不能授权退款/发货或覆盖 Policy。

## 上下文和工单记忆

Settings 默认 context_max_input_tokens=24000、context_output_reserve=1200、context_recent_groups=3。

ContextManager 使用标明的 UTF-8 字节上界估算，预算包含 Schema、消息和保护指令。旧动作/错误确定性摘要，近期 assistant/tool 按完整组保留；证据只带摘录，read_evidence 可分页取回，完整证据不删除。必要保护信息仍超预算时返回 context_budget_exceeded。

CaseStore 是持久工单权威来源，checkpoint 保存记忆，resume 恢复证据与历史动作；替换材料/纠正文字使过期记录失效。未实现原生 LangGraph checkpointer、interrupt 或跨用户向量记忆。

## 客户流程、错误与展示

客户流程提供 evaluate_issues、request_information、finish、escalate 等状态约束动作；建议必须满足当前有效证据、充分性和政策。退货不默认判作损坏，分包待到不默认判作少发。

MCP 工具失败回写 ToolResult；Client 唯一负责有限传输重试，耗尽后 Graph 停止，invalid_tool_calls 可有限修复。状态和诊断通过 SSE、JSON/Markdown 暴露，不展示隐藏思维链。

Web/CLI 复用业务 Service。结果包含 runtime、skills、context_stats、memory，SQLite 保存工单/历史；Web 无公网认证，处理建议 executed=false，不自动执行。

## 交付验证与剩余范围

上次全量离线验证 203 passed，Ruff、pip check 和 wheel 构建成功。真实本地 MCP 被测试，模型调用为 replay/mock。没有本轮真实 API、标注集准确率或成本收益结论。

PDF/Office、RAG、订单系统查询/写入、多租户、分布式队列与原生图审批为后续方向。运行及面试参考分别见根目录 PROJECT_GUIDE.zh-CN.md 与 PROJECT_RESUME.zh-CN.md。
