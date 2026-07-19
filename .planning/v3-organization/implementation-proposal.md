# NextOS V3：组织态势与阶段转场 — 首个实现切片

## 结论

第一步不建设新的“AI 工作流引擎”、阶段状态机或图数据库；只增加内容无关的后台资源队列。

现有 NextOS 已有四个关键底座：Goal Mandate、Operating Brief、追加式结果
收件箱、Goal Cell 树。首个缺口只有一个：Chair 当前所判断的**临时组织态势**
没有作为独立、可持续回看的事实存在。补上它，并加入项目经理这个固定的 AI
能力包，就能把已确认的指挥链落到系统中。

## 一条事实链，五个视图

```text
Human Goal Mandate ──→ Chair / Command Room
                          │
                          ├── Current Operating Brief
                          │   目标、总体方案、当前阶段、决策、未知项、下一步
                          │
                          ├── Current Organization Map   [新增]
                          │   临时工作流、角色、Goal Cell、依赖、返回关系
                          │
                          ├── task() → 一次性专业 AI（独立项可并行，Chair 自行控制≤6）
                          │                  │
                          │                  └── Result Inbox / Goal Tree / runtime facts
                          │                                  │
                          └── 收到阶段性结果 → Project Manager 建议下一阶段
                                                       │
                                                       └── Chair 判断、更新 Brief/Map、继续指挥
```

程序只保存箭头中的原文、运行事实和结果信封；不会识别“阶段”、判断结果好坏或
强制项目经理运行。它只会按数值资源容量接受、FIFO 等待或拒绝任务，绝不读取工作内容。

## 固定角色与临时组织的边界

| 层 | 内容 | 谁决定 | 是否持久 |
|---|---|---|---|
| 角色目录 | Planner、Executor、Fact Finder、Opposition、Recorder、Project Manager 等可复用能力包 | 人和 Chair 通过角色包持续演进 | 是 |
| 临时组织 | 本项目此刻有哪些工作流、由哪个角色承担、各自局部目标和返回关系 | Chair | 当前快照可回看，组织本身可变化 |
| 运行事实 | task 生命周期、结果、Goal Cell 父子关系、运行回合 | 程序如实记录 | 是 |

Goal Tree 只表示线程/Goal Cell 的客观父子结构，不能被程序解释为组织权力、
阶段或完成度；Organization Map 才是 Chair 对“此刻如何组织”的 AI-authored
判断。

## 记录设计

### 继续复用的两条记录

1. **Goal Mandate**：人给出的兴趣、方向、非目标、权限和回到人讨论的边界。
2. **Current Operating Brief**：Chair 完整维护的当前合同。必须包含总体目标、
   总体方案、当前阶段、已做决策及理由、已纳入结果、未知项、下一决策/下一步。

不新增 Phase、Decision Ledger 或“完成状态”表。这些都是语义判断，写入
Operating Brief 后由 Chair 在下一轮读取即可。

### 唯一新增记录：Current Organization Map

新增追加式事件 `organization.map.revised`。它只保存 Chair 提交的完整文本、
哈希、作者 run 和版本；程序不解析、校验或推导文本中的角色、依赖或阶段。

Chair 使用下面的**提示模板**组织内容，但模板不是后端 schema：

```text
# Current Organization Map

## Relation to goal and current phase
- …

## Temporary organization
| workstream / Goal Cell | professional role | local objective | expected return | dependency / handoff |
| --- | --- | --- | --- | --- |

## Current independent work
- … (Chair determines the useful parallel set; at most six concurrent children)

## Return to Chair
- which complete results or facts will change the next decision
```

这样 Chair 能读到清晰的组织形式，人类也能在工作记录面板读到完全相同的原文。
后续只有当“人工也需要可交互图谱”成为真实瓶颈时，才为它增加纯展示用的机械结构；
不会提前把 AI 判断固化为程序图规则。

## 项目经理角色

新增固定角色 `project-manager`，但它是一次性 AI，不是常驻管理器，也没有任何
后端推进权限。

**输入**：Goal Mandate、Operating Brief、Organization Map、该阶段完整结果、
相关事实和人工边界（都由 Chair 在任务 prompt 中提供）。

**返回**：

- 当前阶段改变了什么；
- 下一阶段候选目标、交付物与完成判断材料；
- 哪些事项可并行、哪些确有事实依赖；
- 建议的临时组织/Goal Cell 与需要的角色；
- 风险、未知项及何时应回到人讨论；
- 给 Chair 的一份下一阶段 handoff 草案。

**绝不做**：决定是否启动下一阶段、直接调用其他 AI、改写 Goal Workspace、
确认结果、关闭目标、把“阶段结束”变成程序事件。

Chair 在“一个阶段已有完整报告且下一步需要重新规划”时通常调用它；是否调用、
是否采纳、如何组织、何时并行，都仍是 Chair 的自然语言判断。

## 精确改动范围

| 工作包 | 主要位置 | 结果 |
|---|---|---|
| 组织快照事件 | `backend/packages/harness/deerflow/persistence/workspace_event/` | 添加常量和当前快照投影；不迁移数据库、不添表 |
| Chair 记录工具 | `.../tools/builtins/goal_workspace_tool.py` | `record_goal_workspace(kind="organization_map")` 原样追加文本 |
| Chair 上下文 | `.../agents/middlewares/round_context_middleware.py` | 在现有内部 Workspace 块中原样注入最新 Organization Map |
| Gateway API | `backend/app/gateway/routers/threads.py` | `/goal-workspace` 增加 `organization_map` 字段 |
| 前端工作记录 | `frontend/src/core/threads/goal-workspace.ts`、`frontend/src/components/workspace/chats/thread-work-record.tsx` | 同源解析、展示一个只读“Organization Map”记录；不新增控制入口 |
| 固定角色包 | `command_room_roles.py` 与 `skills/custom/command-room-project-manager/` | 添加 `project-manager` 名称、章程和方法，不改任务运输机制 |
| Chair 方法 | `prompt.py` 与 `skills/custom/nextos-commander/` | 记录 Map、阶段性结果后的 PM 建议、依赖优先串行/独立优先并行；提示 Chair 的 6 条容量 |
| 运行资源池 | `backend/app/gateway/command_room_background.py` | 全局 12 个 child 执行槽、64 个 FIFO 等待槽、每个 `(user, Command Room)` 6 个已接纳 child；不读取 AI 内容 |
| 回归证明 | `test_goal_workspace.py`、`test_threads_router.py`、角色/skill/prompt 测试、前端解析测试 | 证明原文、owner 隔离、API 与 UI 投影、角色边界均不变形 |

现有通用 `workspace_events` 已有 `event_type/body/metadata/hash/version`，
所以 `organization.map.revised` 不需要 SQL migration。Human Owner 已确认这个
持久化 API 字段和 AI-to-AI 角色契约，并授权按此切片实施。

## Chair 的运行方式（非程序流程）

1. Chair 从 Goal Mandate 和最新 Operating Brief 重新锚定总目标。
2. 有实质性新目标时，由 Planner 与 Opposition 协助形成方案；Chair 自己综合。
3. Chair 写入更新后的 Operating Brief 和 Organization Map。
4. 无事实依赖的工作可并行；有依赖的工作串行。一个 Chair 同时保有的等待/运行 child 最多为 6；Gateway 同时执行 12 条，额外 64 条按 FIFO 等待。
5. 子 AI 结束后，程序把完整结果放入 Inbox 并唤醒 Chair；程序不判断下一步。
6. Chair 读取、对比和吸收结果。若阶段性结果足以改变后续路线，Chair 将完整上下文交给 Project Manager 获得下一阶段建议。
7. Chair 判断建议，更新 Brief/Map，并继续派发、补事实、改计划或在真正需要时回到人讨论。

“核对”仍是 Chair 针对风险/冲突/证据不足临时发起的独立 AI 视角，不成为审批门或固定程序步骤。

## 明确不做

- 不建图数据库、工作流引擎、阶段表或审批状态机。
- 不让后端按任务文本、角色、优先级、质量或“阶段”排序、拒绝、分批或自动续跑；队列只按固定数值容量 FIFO 工作。
- 不从结果文字自动抽取角色、依赖、完成度或下一步。
- 不让项目经理替 Chair 决策或驱动 AI。
- 不一次性新增视频文案、代码审查等更多固定角色；等其被反复使用、边界稳定后再加入角色包。

## 验收证据

1. Chair 可原样记录并在下一次模型调用读回 Organization Map。
2. 同一记录可从 `/goal-workspace` 读取，前端显示的正文和 revision 与后端一致。
3. `project-manager` 可被 Chair 显式选择，得到完整角色包；它没有额外工具、路由或推进能力。
4. 不带 Organization Map 的历史线程继续正常工作。
5. 后端 focused tests、前端类型/单测与现有 Prompt/Skill 边界测试通过；`Progress.md` 记录实际结果。

## 后续触发条件，而非预先建设

- **需要图形组织图**：人类无法仅从原文 Map + Goal Tree 做运营判断时，增加纯渲染结构。
- **需要知识图谱**：跨项目、多次历史关联查询确实成为 Chair 的检索瓶颈时，再讨论索引/图存储。
- **需要新固定角色**：某类任务已经反复出现，且有明确 owner、输入、输出、禁止事项与可验证行为时，再加角色包。
