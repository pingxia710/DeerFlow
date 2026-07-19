# Findings: NextOS V3 组织态势与阶段推进

## Requirements

- Command Room 是持续持有目标、取舍与下一步决定的 CEO/Chair。
- 组织形态暂时是临时的；角色是长期、固定且可持续优化的能力包。
- 子 AI 调用是一次性执行；复杂局部目标可通过临时 Goal Cell 保留工作空间和组织记忆。
- 每个阶段执行和核对只完成整体目标的一部分；Chair 必须不断回到总目标和整体方案。
- 项目经理角色在阶段汇报后给出下一阶段规划，供 Chair 判断和指挥。
- 可独立的信息收集优先并行；Chair 的组织原则是最多六条并行。
- Human Owner 已确认运行容量：后台全局最多同时执行 12 条，等待队列最多 64 条；单个 Command Room 最多同时挂起 6 条。它们是内容无关的资源上限，不是质量/阶段/路由判断。
- 程序只保存、传输、运行、结束、隔离和唤醒；可以按用户确认的固定数值进行 FIFO 资源排队，但不能解释、评价、语义路由、推进、验收或关闭 AI 工作。

## Research Findings

- 已有 Goal Workspace：追加式 Goal Mandate、Operating Brief、Result Inbox 和 Goal Cell 事实记录。
- 已有递归 Goal Cell：子 Cell 独立线程、完整结果回父级 Inbox、自动事实唤醒。
- 已有固定角色包：Planner、Executor、Fact Finder、Opposition、Recorder，以及 Chair 包；角色由 Chair 选择，程序不选。
- 已有前端 Goal Workspace、Result Inbox、Goal Tree 与 Activity 面板投影。
- 已有 `round_id` / `round_context` 和工作记录投影，可作为“当前一次 Chair 指挥回合”的客观运行关联；它不能被解释为程序定义的阶段流程。
- `workspace_events` 已经是通用追加式表：保存 `event_type`、AI-authored `body`、元数据、作者 Run、哈希和版本；不需要新存储或图数据库即可追加组织/决策/阶段建议记录。
- 当前 `current_context()` 只投影 Goal Mandate 与 Operating Brief；第一版若加入 Organization Map、阶段记录和 Decision Ledger，需要显式扩展这个确定性投影，而不能让程序解释历史文本。
- `record_goal_workspace` 目前只允许 Chair 写入 `goal_mandate` 与 `operating_brief`；它是最小扩展点，或可新增一个同样只追加原文的 Chair 记录工具。
- 当前固定角色映射含 planner/executor/fact-finder/opposition/recorder，尚无项目经理角色包。
- 当前项目明确禁止用程序定义阶段、验收、角色路由、自动检查、返工或结案。
- 当前 Operating Brief 的 prompt 定义已经覆盖“计划、决定、未知项、已纳入结果”，并以完整原文注入后续 Chair；因此当前阶段、阶段性方案、决策依据和下一步不应再建独立后端状态。
- 工作记录面板已同时展示 Goal Mandate、Operating Brief、结果收件箱和 Goal Tree；Organization Map 可作为同一接口的第三个只读记录区，无需另起 CEO 页面、图组件或调度按钮。
- 角色包由 `COMMAND_ROOM_ROLE_CONFIGS` 与 `COMMAND_ROOM_ROLE_SKILLS` 的显式映射装配，`task` 仅把 Chair 选择的角色包原文带入子 AI；项目经理可沿此路径加入，而不会获得程序性推进权。
- 当前 `CommandRoomBackgroundService` 对每个已接纳任务直接 `asyncio.create_task`，没有并发池或等待队列；这是 12/64/6 唯一需要新增的运行层缺口。
- 现有 TaskLane 已支持内容无关的 `pending` / `in_progress` 运行状态和任意 `handoff` 事实，无需数据库迁移即可记录“queued/running”资源状态。
- Gateway 背景服务和恢复机制本来就是进程内执行器；重启时无法恢复 Python callable，会把未产生结果的任务记为事实失败。因此第一版队列也必须保持进程内，不能假装为跨 Gateway 的持久队列。

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| 以现有 Workspace 事件作为组织态势第一版的事实底座 | 复用已有 owner-scoped、追加式持久化和前端投影模式，避免新存储系统。 |
| Chair 编写当前 Organization Map / Decision / Phase 记录 | 关系、优先级和语义仍由 AI 判断；程序只保存类型、ID、版本和原文。 |
| 以“阶段建议”而非“自动下一阶段”表达项目经理产物 | 保持项目经理辅助 Chair，不成为程序或固定流程的决策者。 |
| 当前阶段、决策与下一步复用 Operating Brief | 现有字段已是 Chair 的完整当前合同；第一版不新增 Phase 或 Decision Ledger 事件。 |
| Organization Map 是第一版唯一新增 Workspace 记录 | 它使临时组织能作为独立事实被 Chair 和工作记录同时看到；其正文不由程序解析。 |
| 12/64/6 在现有 Gateway service 内实现 | 用 FIFO 运行队列执行 child process；全局 worker=12、等待项=64、每 `(user, thread)` 已接纳 child=6。队列只看身份和容量，绝不读取 AI 文本。 |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
|       |            |

## Resources

- `AGENTS.md`
- `backend/AGENTS.md`
- `backend/docs/agent-development-reference.md`
- `Progress.md`
- `backend/app/gateway/goal_cells.py`
- `backend/packages/harness/deerflow/persistence/workspace_event/`
- `backend/packages/harness/deerflow/tools/builtins/goal_workspace_tool.py`
- `backend/packages/harness/deerflow/agents/middlewares/round_context_middleware.py`
- `backend/packages/harness/deerflow/subagents/builtins/command_room_roles.py`
- `backend/app/gateway/routers/threads.py`
- `frontend/src/components/workspace/chats/thread-work-record.tsx`

## Visual/Browser Findings

- 本轮尚未进行浏览器或视觉检查。
