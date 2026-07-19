# NextOS V3 第二切片最终方案：事实历史与 Chair 上下文恢复

> 状态：**Human Owner 已确认；第二切片已按本方案实施并完成非生产验证。**
>
> 永久边界不变：程序只能保存、传输、执行、取消、超时、隔离、记录运行事实和唤醒；不得理解 AI 文本、评价结果、选择角色、定义阶段、自动推进或关闭工作。

## 1. 结论

第二切片应只补一件 P0 能力：**让 Chair 和人类驾驶舱按需、只读、分页地取回同一 Thread 内的追加式 Workspace 事实历史**。

现有系统已经能保存这些历史，但正常 Chair 上下文只自动注入最新的 Goal Mandate、Operating Brief、Organization Map；驾驶舱的 Result Inbox 也只返回尚未被 Chair 明确确认的结果。因此，长周期项目在当前 Brief 不足以解释旧决策、旧 Organization Map 或已确认结果时，缺少一条受支持的事实恢复路径。

这不是新工作流、阶段表、知识图谱或项目经理流程。它是对现有 `workspace_events` 的最小只读投影。Chair 决定何时读取、阅读哪些事实、如何理解、是否更新 Brief/Map、是否调用 Project Manager、以及下一步做什么。

## 2. 当前端到端链路

### 已验证事实

```text
Human Goal Mandate
  │ Chair 以原文追加记录
  ▼
Current Operating Brief + Current Organization Map
  │ 每次 Chair Run 自动获得三条最新原文
  ▼
Chair 判断并选择 task() 或 create_goal_cell()
  │
  ├─ task(): 角色包 + 完整 Chair prompt → 背景 FIFO 容量事实
  │       queued / running / finished
  │
  └─ Goal Cell: 继承 Mandate + Chair 给出的完整本地 Brief；
          可递归，完成后以完整结果返回父 Inbox
  ▼
完整 child result 原样追加到 Result Inbox
  ▼
Gateway 仅以事实唤醒新的顺序 Chair Run
  ▼
Chair 读取结果、作出自己的判断；必要时调用 project-manager
  ▼
Project Manager 返回“下一阶段建议”这一普通完整结果
  ▼
Chair 决定是否采纳，更新 Brief/Map，继续或在真实边界变化时回到 Human
```

| 链路环节 | 已验证实现 |
| --- | --- |
| Goal Mandate / Brief / Map | `workspace_events` 追加式、owner-scoped、带 revision/hash/author；`current_context()` 返回每类最新记录。 |
| Chair 上下文 | `CommandRoomRoundContextMiddleware` 原样注入三类当前记录，不解析正文。 |
| task 与容量 | `task()` 仅在 Chair 运行时交给背景服务；全局 12 个执行槽、64 个等待槽、每个 `(user, Command Room)` 最多 6 个 queued/running，FIFO 只读身份和固定数值。 |
| Goal Cell | 父 Cell 以完整本地 Brief 创建子 Cell；子 Cell 以完整自然语言结果和 artifact refs 返回父级，再走同一背景唤醒路径。 |
| Result Inbox / wake | 每个 child 返回单独保存；并发结果可合并一次 wake 信号而不合并正文；Chair acknowledgement 是交付事实，不是验收。 |
| Project Manager | 固定 `project-manager` 角色包存在；其章程禁止启动工作、派发 AI、写 Workspace、批准结果、推进阶段或关闭目标。 |
| 人类驾驶舱 | 当前工作记录展示最新 Mandate/Brief/Map、未确认 Result Inbox、Goal Tree、运行状态与事件事实；浏览器测试确认没有 approve/accept/acknowledge 控件。 |

### 已验证的缺口

- `WorkspaceEventRepository.list_by_thread()` 已保存可读历史，但没有被 Chair 工具或 Goal Workspace HTTP API 暴露。
- `current_context()` 有意只给最新三条记录；这对常规新一轮足够，但不能按需恢复旧 Brief/Map。
- `/goal-workspace` 调用 Result Inbox 的默认游标，因此返回的是 Chair 上次 acknowledgement 之后的结果；已确认结果仍在存储中，但不在驾驶舱当前接口中。

### 推断

- 真实持续项目不能依赖“最新 Brief 永远足够”的隐含假设。Chair 应能在上下文不足、旧决策仍相关、或 Human 追问历史依据时读取原始事实，而不是由程序摘要或猜测历史。
- 自动把全部历史塞进每次 prompt 会造成上下文膨胀和旧信息干扰；应保留当前的“最新三条自动注入 + Chair 按需读取”的模型。

## 3. 缺口优先级

| 优先级 | 内容 | 处理结论 |
| --- | --- | --- |
| **P0** | 同一 Goal Workspace 的旧 Mandate/Brief/Map、已确认结果、通知/确认事实没有统一的只读恢复路径。 | 本第二切片实现无语义分页历史读，并把其使用方式写入 Chair/PM handoff。 |
| **P0** | 长周期 handoff 缺少明确的“当前 Brief 是压缩索引，历史按需读取；Goal Cell/PM 接收 Chair 选择的完整事实包”的操作约定。 | 只改 Prompt/Skill/角色章程和测试；不以代码检查 handoff 是否“足够”。 |
| **P1** | 驾驶舱尚未把单个 Command Room 的 queued/running/finished 容量事实做成清晰概览。 | 先复用现有 TaskLane/运行快照；只有用户实际需要排障或观察资源时，再增加标注为 Gateway-local 的纯事实视图。 |
| **P1** | 结果与产物的跨 revision 阅读体验仍是长文本。 | 先提供历史列表、revision、来源、hash、原文与现有 artifact refs；不做自动摘要、证据等级或结果评分。 |
| **Later** | 交互式组织图。 | 只有 Map 原文 + Goal Tree 已无法让 Human 理解组织/依赖时，纯渲染 Chair-authored 结构；不反向驱动调度。 |
| **Later** | 项目知识检索或 RAG。 | 先记录 Chair 因本地文件/Workspace 历史过大而无法有效检索的真实案例；届时建设只读、可溯源的项目索引，不替代当前事实记录。 |
| **Later** | 跨项目索引。 | 只在多个根项目确实需要复用知识，并且 Human Owner 已决定可见性、权限与保留边界后讨论。 |
| **Later** | 多 Gateway 持久队列。 | 当前 child queue 明确是单 Gateway、进程内 FIFO。只有多实例执行或重启后必须继续 child callable 成为真实需求时，另行确认 durable dispatcher/lease 架构。 |
| **不应该做** | 工作流引擎、阶段状态机、图数据库、PM 定时器/服务、审批/验收/评分、自动角色选择、语义路由、自动 Skill 修改、持久化消息队列。 | 全部不属于本切片，且会越过永久程序边界。 |

## 4. 第二切片的最小改动

### 4.1 数据与后端：一条无语义历史读接口

复用现有 `workspace_events` 表；**不加迁移、不加表、不加 event type、不解析正文**。

1. 在 `backend/packages/harness/deerflow/persistence/workspace_event/{sql,memory}.py` 增加 owner-scoped、按 revision 倒序的 bounded history read：
   - 输入只有 `thread_id`、`user_id`、`before_revision`、`limit`；
   - 输出是完整原始事件 envelope：`revision`、`event_type`、`body`、`metadata`、`content_hash`、`author_run_id`、`created_at`；
   - 输出机械的下一页 cursor；不含摘要、阶段、重要性、质量或“下一步”。
2. 在 `goal_workspace_tool.py` 增加仅 Chair 可用的 `read_goal_workspace_history(before_revision?, limit?)`：
   - 将上述 records 原样排版给 Chair；
   - 保留现有 `read_workspace_results()` 和 acknowledgement 语义不变；
   - 不自动在每一轮调用，也不自动确认任何结果。
3. 在 `backend/app/gateway/routers/threads.py` 新增 owner-scoped 的只读历史端点，例如：
   `GET /api/threads/{thread_id}/goal-workspace/history?before_revision=&limit=`。
   - 现有 `/goal-workspace` 继续代表“当前三条记录 + 未确认 Inbox”，保持兼容；
   - 历史端点返回所有 event types，包括 `result.received`、notification 与 acknowledgement 这些客观交付事实。

### 4.2 Chair、Goal Cell 与 Project Manager：只补 handoff 合同

更新 `prompt.py`、`skills/custom/nextos-commander/{AGENTS.md,SKILL.md}`、
`skills/custom/command-room-project-manager/{AGENTS.md,SKILL.md}`：

- 每次 Chair Run 仍自动收到最新 Mandate/Brief/Map；只有它自己认为需要旧事实时才调用 bounded history read。
- Operating Brief 是 Chair 对当前目标、已采纳事实、决定及理由、未决项、下一步和相关 revision/artifact refs 的**当前压缩索引**；它不是程序状态机。
- Organization Map 仍只记录 Chair 当前判断的临时组织、事实依赖与返回关系；不要由 Goal Tree 或队列推导。
- 创建 Goal Cell 时，Chair 在 `brief` 中传入本地目标真正需要的 Mandate/Brief/Map/历史摘录和 exact input refs；程序不自动把整个父 Workspace 或全部 Map 复制进子 Cell。
- 调用 Project Manager 时，Chair 显式给出：当前 Mandate、Brief、Map、相关事实的 revision/完整正文、artifact refs、Human 边界和它希望 PM 回答的问题。PM 的输出必须区分事实与假设、给出建议的并行/真实依赖、风险、回人条件及完整 handoff 草案，但仍无启动权。
- Skill 优化沿用现有“AI 识别有证据的重复失败 → 最小层修正 → 正/反例检查 → 记录 Progress”的治理方法。程序不检测模式、不自动改 Skill，也不把 SkillOpt 变成运行门禁。

### 4.3 前端：事实浏览，不做驾驶

在 `frontend/src/core/threads/goal-workspace.ts`、`api.ts`、`hooks.ts`、
`thread-work-record.tsx` 增加一个惰性历史查询和 History 区：

- 首屏保持当前视图：最新 Mandate/Brief/Map、未确认 Inbox、Goal Tree、运行事实；
- 展开 History 后按页加载最近事实，显示 revision、时间、作者 run、event type、来源/角色/任务元数据和完整原文；“加载更早事实”只是分页读取；
- 已确认的完整结果与 acknowledgement/notification 事实在 History 中可追溯，不把 acknowledgement 翻译成“已验收”；
- 不新增 approve、accept、acknowledge、advance phase、dispatch role、choose role、reorder/priority、自动 retry、mark complete 等语义控制按钮。

现有 Refresh、关闭面板、展开详情、分页读取是事实查看/界面操作，可保留。若未来已授权的运行取消 UI 存在，它必须是明确的系统生命周期/权限动作，不能借“取消”判断工作质量或推进项目；本切片不新增它。

### 4.4 明确不改

- `command_room_background.py` 的 12 / 64 / 6 内容无关容量与 FIFO 行为；
- `task()` 的角色选择和背景执行运输；
- Goal Cell 的隔离、输入胶囊和父子返回语义；
- `project-manager` 作为一次性 role 的身份，不把它变成持久服务；
- Workspace 当前上下文的“只注入最新三条”策略。

## 5. 最终运行方式

1. Human 提供或修订 Goal Mandate；Chair 原样记录。
2. Chair 自动获得当前 Mandate、Brief、Map，必要时只读加载一页旧 Workspace 事实。
3. Chair 自己形成/修订 Operating Brief 和 Organization Map，自己选择独立并行或真实依赖串行。
4. `task()` / Goal Cell 经现有 12/64/6 容量运行，程序只记录 queued/running/finished 和结果信封。
5. 结果原样进入 Inbox，事实 wake 创建下一顺序 Chair Run；Chair 可从 Inbox 或历史读取所有需要的完整结果。
6. 当完整阶段事实改变路线时，Chair 选择是否派发 Project Manager；其建议像任何 child result 一样进入 Inbox/history。
7. Chair 比较事实、作出下一步判断、更新 Brief/Map 并继续；只有目标、现实权限、材料边界或不可逆后果改变时回到 Human。

没有程序定义的“阶段完成”或自动 PM 调用。上面的编号是 AI 的运行方法，不是代码状态转换。

## 6. 验收方式

### 新增覆盖

1. SQL 与 memory store：owner isolation、revision cursor、分页无重无漏、所有正文/hash/metadata 原样返回。
2. Chair tool：非 Chair 不可读；Chair 可分页读到历史 Mandate/Brief/Map、已确认结果和 acknowledgement 事实；读取不改变任何状态。
3. HTTP：历史端点必须 owner-scoped；现有 `/goal-workspace` 的 current/pending 行为不变。
4. Prompt/role packages：Chair 在需要时按需读取历史；PM 输入/输出仍是建议且无启动、确认、推进或关闭权。
5. Frontend unit：解析保留完整文本和 cursor；历史与当前 Inbox 不互相替代。
6. Playwright：Work Record 能查看已确认历史结果和 delivery facts；仍不存在 approval/accept/acknowledge/advance 等控制。
7. 回归：现有 background capacity、result Inbox、Goal Cell、role-package、task transport、prompt 断言保持通过；运行 NextOS SkillOpt 静态 probe。

### 本轮已完成的非生产核查

- 后端相关链路：48 passed（Goal Workspace、background FIFO/wake、Goal Cell、Prompt、role package、线程当前/Goal Tree API）。
- 前端事实解析/API：8 passed。
- Playwright：`work record exposes the AI organization without approval controls` 通过。
- NextOS SkillOpt 静态 probe：train / val / test 的 hard 与 soft 均为 1.0。
- 未访问或修改生产/外部业务系统；未暴露凭据、客户或支付数据。

## 7. Human Owner 已确认的决策

1. 第二切片范围仅为“Workspace 事实历史与上下文恢复 + 最小 Prompt/Skill/驾驶舱只读投影”；P1、Later 与“不应该做”项目均不实施。
2. 同一 Thread 的已确认完整结果继续追加式全量保留，授权 owner 可在 History 中按需分页读取。acknowledgement 只代表投递事实，不代表验收、完成、删除或隐藏。

## 8. 实施与验证记录

- 复用现有 `workspace_events`，未新增数据库表、迁移或 event type。SQL 与 memory 的 `history()` 都按精确 owner、revision 倒序和独占 `before_revision` cursor 返回 1–100 条完整原始 event envelope。
- 新增仅 Chair 可调用的 `read_goal_workspace_history`，以及 owner-scoped、只读的 `GET /api/threads/{thread_id}/goal-workspace/history`。`/goal-workspace` 的当前三条记录和未确认 Inbox 语义未变；History 包含已确认结果及 notification/acknowledgement 事实。
- 驾驶舱 History 是惰性查询：首屏不请求历史；展开后才加载一页，"加载更早事实" 只读取下一页。显示 revision、时间、author Run、event type、metadata 和完整原文，没有 approval、accept、acknowledge、advance 或 dispatch 控件。
- Prompt 与 Chair/Project Manager 角色包只补 handoff 合同：Chair 自己按需选择历史；Brief 是 AI 当前压缩索引；Goal Cell 与 PM 接收 Chair 明确选出的完整事实，不存在程序自动复制、选择或解释。
- 验证结果：后端相关 Workspace/API/background/Goal Cell/prompt/role 回归 **235 passed**（1 个 TestClient deprecation warning）；前端单元 **829 passed**；`pnpm check`、目标 Prettier、`git diff --check` 通过；Work Record Playwright **6 passed**；SkillOpt static probe 在 train / val / test 的 hard 与 soft 均为 **1.0**。
- 已执行本切片文件的 Ruff lint/format 检查。全仓 `make lint` 仅被预先存在且本轮未触及的 `backend/app/gateway/command_room_background.py` 格式差异阻断；未改动其 12/64/6 或 FIFO 行为。

## 9. 第三切片确认点

本切片到此停止。未实施工作流、阶段状态机、图数据库、RAG、组织图、持久队列、审批、评分、自动角色选择、自动 Project Manager 或任何第三切片内容；后续扩展必须获得新的 Human Owner 确认。
