# CaseLens 运行时实施记录

更新日期：2026-10-12。项目：WSL Ubuntu-24.04，/home/tcpanda/projects/caselens。

## 已完成

- LangGraph 0.6.11 StateGraph 替换手写主循环，包含 bootstrap/load_skills/prepare_context/controller/tools/checkpoint/finalize。
- MCP SDK 1.30.0 stdio 图片服务，实际 discovery/call_tool；裁剪、缩放、旋转和派生图恢复。
- 5 个场景 Skill，数量不足包包含参考清单、Schema、分包示例和固定可信脚本。
- 上下文预算、输出预留、完整调用组、结构化摘要、关键证据及同工单恢复。
- 退货独立意图、wrong/split/return 回放；分包待到不提前建议补发。
- Web/CLI 接入，新增 skills/mcp-server 命令、/api/skills 及 Graph/上下文诊断。
- 修复 Controller 传输失败反复重试、错误反馈丢失、多次续办摘要累计、非法 evidence IDs 和 MCP 启动异常收口。

## 验证结果

上次最终验证：203 passed，1 个 LangGraph 上游弃用 warning；Ruff 全部通过，pip check 无冲突，wheel 构建成功。图片 MCP 独立套件 18 passed，Skill 加载器独立套件 30 passed。

使用现有 replay/mock 测试与真实本地 stdio MCP，不调用真实模型；Jev 当前不可连接。浏览器既有回归通过，本轮环境缺少 Node/Playwright，未重跑，详见 verification.md。

## 实施取舍

- 使用已发布且实际验证的 MCP 1.30.0，不照搬早期方案中的未核验 2.x API。
- CaseStore 保持工单权威存储，不增加相互竞争的图存储。没有原生 LangGraph checkpointer/interrupt。
- 结构化摘要不依赖额外付费模型；估算用 UTF-8 字节上界，不宣称精确 token 数。
- 只改 WSL，不同步 Windows 旧项目；该目录没有 Git 仓库，不初始化或创建含密钥快照。
- 不自动退款、补偿或发货，不提供公网认证、多租户或真实效果指标。

## 文档更新

已同步 README.md、README.zh-CN.md、PROJECT_GUIDE.zh-CN.md、PROJECT_RESUME.zh-CN.md、当前规格与原设计的实施状态。保留原 GitHub 调研证据和历史验证，不把计划改写成实测结论。

运行详解见 ../PROJECT_GUIDE.zh-CN.md；简历与面试问答见 ../PROJECT_RESUME.zh-CN.md。原方案：superpowers/specs/2026-10-12-runtime-design.md；实施计划：superpowers/plans/2026-10-12-runtime.md。
