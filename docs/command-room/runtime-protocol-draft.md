# DeerFlow Multi-conversation / Command Room Runtime 事件与证据协议草案

> 状态：草案 / design artifact。本文只定义不冲突的运行时事件、证据与展示边界建议，用于后续讨论；不要求马上落库、不要求修改 migration、不改变当前 checkpoint、权限、artifact 存储或前端行为。

## 0. 背景与设计边界

轻量参考了现有 Command Room 文档与运行时代码：

- `docs/command-room/multi-conversation-runtime-contract.md`
- `docs/command-room/run-protocol.md`
- `docs/command-room/core-invariants.md`
- `backend/docs/command-room/subtask-interfaces.md`
- `backend/packages/harness/deerflow/command_room/round.py`
- `backend/packages/harness/deerflow/command_room/evidence.py`
- `backend/app/gateway/routers/thread_runs.py`

本草案沿用现有第一阶段原则：AI-AI 协作记录归属于父执行上下文，即 `parent thread_id + parent run_id + task_id`。不要在本草案中引入 `child_thread_id`、新的 checkpoint namespace、自动 reviewer gate、自动 PASS/FAIL 或自动迁移。

## 1. 协议目标

1. 给 Thread / Run / Task / Action / Subagent / Artifact / Evidence / Audit / Round 提供最小共同语言。
2. 明确 owner/provenance 字段，使事件可追溯、可过滤、可迁移。
3. 分清三条历史：Thread timeline、Run timeline、Checkpoint history。
4. 把证据强弱作为 Chair/Lead AI 的判断输入，而不是程序自动验收。
5. 为后端 API、前端 state 分流和 migration 兼容给出低冲突建议。

## 2. 通用字段约定

所有事件型或可引用对象建议尽量包含以下通用字段。老数据可以缺省，读取端应容错。

| 字段 | 含义 |
| --- | --- |
| `user_id` | 所属用户或租户。用于权限、存储根和反枚举，不应从可见文本推断。 |
| `thread_id` | 用户会话根。Thread timeline 与 checkpoint 根都围绕它组织。 |
| `run_id` | 一次执行尝试。Run timeline 的主要过滤键。 |
| `task_id` | 某个 task lane / subtask 的局部标识。没有 task 时可为空。 |
| `action_id` | 一个工具调用、子任务调度、验证动作或可执行动作的标识。没有 action 时可为空。 |
| `round_id` | Command Room 一轮授权/判断循环的标识。没有 round 时可为空。 |
| `seq` | 在所属 timeline 内单调递增的顺序号；用于恢复展示顺序。 |
| `created_at` | 事件创建时间，ISO 8601。排序以 `seq` 优先，时间作为补充。 |
| `kind` | 对象/事件类别，如 `task.event`、`action.result`。 |
| `surface` | 展示/控制表面：建议至少支持 `chat`、`control`、`task`、`artifact`、`audit`、`hidden`。 |
| `visible_in_chat` | 是否默认进入用户聊天流。控制消息、审计信号和内部 task 事件默认 false。 |
| `provenance` | 结构化来源，如 producer role、tool、runtime adapter、source event id、input refs。 |

建议的 provenance 子字段：

```json
{
  "producer": "runtime|lead_agent|subagent|tool|gateway|frontend",
  "role": "chair|planner|boundary|evidence|opposition|executor|recorder|null",
  "source": "model_output|tool_output|command|file|api|system",
  "source_event_id": "...",
  "parent_event_id": "...",
  "derived_from": ["event-or-ref"],
  "redaction": "none|summary|hash|omitted"
}
```

## 3. 最小对象定义

### 3.1 Thread

用户可见会话容器，也是 checkpoint 的稳定根。

关键字段：

- `user_id`
- `thread_id`
- `created_at`, `updated_at`
- `title` / `metadata`
- `default_surface`: 通常为 `chat`
- `latest_run_id`
- `provenance`: 创建来源，如 UI、API、import

语义：Thread 跨越多个 Run。用户重新打开会话时首先看到 Thread timeline，而不是某个 Run 的完整内部事件流。

### 3.2 Run

Thread 内的一次执行尝试。

关键字段：

- `user_id`, `thread_id`, `run_id`
- `assistant_id`
- `status`: `pending|running|completed|failed|cancelled|timed_out|blocked`
- `terminal_reason`
- `created_at`, `updated_at`
- `metadata`, `kwargs`
- `seq_base` / `last_seq`（可选）
- token / model / caller 统计（可选）
- `provenance`

语义：Run timeline 用于聚合该次执行内的 chat.message、control.message、task.event、action.result、subagent.finding、artifact.event、audit.signal、round.state。

### 3.3 TaskLane

一个父 Run 内的可并行/可追踪工作 lane。它不是子会话，不自动拥有 checkpoint。

关键字段：

- `user_id`, `thread_id`, `run_id`, `task_id`
- `round_id`（可选）
- `title` / `description`
- `assignee`: role 或 subagent type，如 `executor`、`fact-finder`
- `status`: `pending|running|completed|failed|blocked|cancelled|timed_out`
- `created_at`, `updated_at`
- `seq`
- `surface`: 默认 `task`
- `visible_in_chat`: 默认 false，可由摘要事件决定是否显示
- `provenance`

语义：TaskLane 是 parent run 的工作分支。多个 lane 可并行，但写入共享资源时仍需要上层边界控制。

### 3.4 TaskEvent

TaskLane 生命周期和进度事件。

关键字段：

- `event_id`
- `user_id`, `thread_id`, `run_id`, `task_id`, `round_id`
- `seq`, `created_at`
- `kind`: 固定为 `task.event`
- `event_type`: `task_started|task_progress|task_completed|task_failed|task_cancelled|task_timed_out|task_blocked`
- `status`, `terminal_reason`
- `summary`（默认应为脱敏摘要）
- `artifact_refs`, `evidence_refs`, `output_refs`
- `surface`: 默认 `task`
- `visible_in_chat`: 默认 false
- `provenance`

语义：TaskEvent 记录“发生了什么”，不要求 sub-AI 填表，不自动代表验收。

### 3.5 ActionResult

由 runtime/adapter 归一化的动作结果，主要来自工具、命令、文件、日志或 subtask terminal output。

关键字段：

- `result_id`
- `user_id`, `thread_id`, `run_id`, `task_id`, `action_id`, `round_id`
- `seq`, `created_at`
- `kind`: `action.result`
- `description`
- `status`, `terminal_reason`
- `summary`
- `evidence_refs`
- `output_ref`
- `artifact_refs`
- `risks`, `conflicts`, `open_questions`
- `error`
- `surface`: 默认 `task` 或 `control`
- `visible_in_chat`: 默认 false
- `provenance`

语义：ActionResult 是事实容器，不是 worker 手写格式，也不是 PASS/FAIL 判定。

### 3.6 SubagentFinding

subagent 的结论、观察、反对意见或 handoff 结果。

关键字段：

- `finding_id`
- `user_id`, `thread_id`, `run_id`, `task_id`, `action_id`, `round_id`
- `seq`, `created_at`
- `kind`: `subagent.finding`
- `source_role`, `target_role`
- `summary`
- `raw_output_ref` 或 `handoff_file`（重要工作优先用文件承载）
- `evidence_strength`: `Strong|Weak|Unverified`
- `evidence_refs`, `artifact_refs`, `output_refs`
- `boundary_status`
- `recommended_next_decision`
- `surface`: 默认 `control` 或 `task`
- `visible_in_chat`: 默认 false，Chair 摘要可另行进入 chat
- `provenance`

语义：SubagentFinding 是 AI-AI 信号。上游原始输出应保持为下游输入的一部分；抽取字段只是索引提示，不能替代原文。

### 3.7 ArtifactRef

对产物的可迁移引用。当前可继续兼容字符串；新结构建议可选引入。

关键字段：

- `artifact_ref`: 兼容旧字符串，如 path / virtual path
- `artifact_id`（可选）
- `user_id`, `thread_id`, `run_id`, `task_id`, `action_id`, `round_id`
- `created_at`
- `kind`: `artifact.ref` 或在事件中作为 ref
- `artifact_type`: `file|image|html|log|diff|patch|report|unknown`
- `uri` / `virtual_path`
- `mime_type`, `size`, `sha256`（可选）
- `storage_root` / `trust_boundary`
- `surface`: 默认 `artifact`
- `visible_in_chat`: 取决于引用它的事件
- `provenance`

语义：ArtifactRef 可作为强证据的一部分，但仅有“某个 output_ref”而无可复现内容/路径/哈希时应视为弱。

### 3.8 EvidenceRef

对可支持某个 claim 的证据引用。

关键字段：

- `evidence_ref`: 兼容旧字符串
- `evidence_id`（可选）
- `user_id`, `thread_id`, `run_id`, `task_id`, `action_id`, `round_id`
- `created_at`
- `kind`: `evidence.ref`
- `claim`（可选）
- `source_type`: `command_output|log|artifact|source_ref|diff|screenshot|test_report|model_summary|worker_claim|external|unknown`
- `strength`: `Strong|Weak|Unverified`
- `ref`: path、URL、event id、artifact id、log id 等
- `excerpt` / `line_range` / `hash`（可选）
- `surface`: 默认 `audit` 或 `control`
- `visible_in_chat`: 默认 false
- `provenance`

语义：EvidenceRef 的 strength 是证据信号强弱，不是最终质量判定。

### 3.9 AuditSignal

短小、可索引的风险/边界/证据缺口/权限信号。

关键字段：

- `signal_id`
- `user_id`, `thread_id`, `run_id`, `task_id`, `action_id`, `round_id`
- `seq`, `created_at`
- `kind`: `audit.signal`
- `signal_type`: `boundary|permission|evidence_gap|risk|conflict|redaction|capability|migration|compat`
- `severity`: `info|warning|blocker`
- `summary`
- `evidence_refs`, `artifact_refs`
- `recommended_action`: `continue|needs_more|ask_user|stop|stop_confirm`
- `surface`: 默认 `audit`
- `visible_in_chat`: 默认 false
- `provenance`

语义：AuditSignal 是提醒和硬边界输入；程序可执行硬权限 stop，但不应据此自动判断项目 PASS/FAIL。

### 3.10 RoundState

Command Room 当前授权/判断循环的轻量状态。

关键字段：

- `round_id`
- `user_id`, `thread_id`, `run_id`
- `seq`, `created_at`, `updated_at`
- `kind`: `round.state`
- `goal`
- `boundary`
- `capability_release`
- `evidence_standard`
- `risk_class`: `Small|Ordinary|High-impact`
- `status`: `open|executing|verifying|needs_more|blocked|stop_confirm|completed`
- `actions`: task/action 摘要或 ids
- `evidence_strength`: 当前聚合强度 `Strong|Weak|Unverified`
- `evidence_refs`, `artifact_refs`
- `open_questions`, `risks`, `conflicts`
- `next_step`, `recommended_next_decision`
- `surface`: 默认 `control`
- `visible_in_chat`: 默认 false，可由 Chair 输出摘要进入 chat
- `provenance`

语义：RoundState 是工作记忆，不是 workflow gate。`is_complete` 只能是机械 readiness hint，最终继续/验收/停止由 Chair/Lead AI 决定。

## 4. 事件分类

建议事件 `kind` 使用以下最小分类：

| kind | 用途 | 默认 timeline | 默认 surface | 默认 `visible_in_chat` |
| --- | --- | --- | --- | --- |
| `chat.message` | 用户/助手可见聊天消息 | Thread + Run | `chat` | true |
| `control.message` | 系统、summary、handoff、loop warning、todo 等控制信息 | Run | `control` 或 `hidden` | false |
| `task.event` | task lane 生命周期和进度 | Run | `task` | false |
| `action.result` | 工具/命令/subtask 结果归一化 | Run | `task` / `control` | false |
| `subagent.finding` | subagent 自然语言发现、反对、证据检查、handoff 输出 | Run | `control` / `task` | false |
| `artifact.event` | artifact 创建、更新、引用、删除、解析失败 | Run + Artifact index | `artifact` | false |
| `audit.signal` | 证据、边界、权限、风险、兼容性信号 | Run | `audit` | false |
| `round.state` | Command Room round 工作状态快照/增量 | Run | `control` | false |

展示原则：用户聊天流只默认展示 `chat.message`。其他事件可以在调试面板、任务面板、审计面板、artifact 面板中展示；如需进入聊天，应由 Chair/Lead AI 生成可读摘要，而不是直接暴露内部事件。

## 5. 三条历史边界

### 5.1 Thread timeline

- 根键：`thread_id`，权限上还需 `user_id`。
- 内容：用户预期重新打开会话时看到的 durable conversation stream。
- 可包含：跨 run 的用户消息、助手最终消息、必要的可见 artifact 摘要。
- 不应混入：所有内部 task 进度、subagent 原始输出、audit 细节、checkpoint 快照。

### 5.2 Run timeline

- 根键：`thread_id + run_id`。
- 内容：一次执行尝试内的完整事件流。
- 可包含：chat.message、control.message、task.event、action.result、subagent.finding、artifact.event、audit.signal、round.state。
- 用途：调试、任务面板、AI-AI handoff continuity、证据追溯、run 级 token/状态统计。
- 注意：Run timeline 不是 checkpoint 隔离边界。

### 5.3 Checkpoint history

- 当前语义：`thread_id + checkpoint_ns + checkpoint_id`，其中 `checkpoint_ns` 当前基本为 `""`。
- `run_id` 目前不是 checkpoint isolation key。
- subagent 隔离当前依赖运行时配置，例如不使用父 checkpointer，而不是分配 child thread 或 checkpoint namespace。
- 后续如引入非 root namespace，必须先定义 list/latest/title/regenerate/rollback 如何在 root 与非 root namespace 间选择或聚合。

## 6. Evidence 强弱定义

### 6.1 Strong evidence

可支持验收/PASS 的证据必须是可复现、可定位、可由工具或运行时观察到的硬信号，例如：

- 命令/测试输出，包含命令、exit code、stdout/stderr 或日志引用。
- 具体日志文件、运行日志片段、事件 id、JSONL 行引用。
- source refs：文件路径 + 行号/范围，或 diff/patch。
- artifact refs：可解析路径、mime、hash、生成上下文，必要时含截图/报告。
- 哈希、校验和、可下载产物、结构化测试报告。
- 前端截图、录屏或可复现实验步骤与结果。

Strong 只说明“证据可用于严肃判断”，不等于程序自动判定质量通过。

### 6.2 Weak evidence

只能作为弱证据、不能单独支持验收：

- worker/subagent 自称“已完成”“测试通过”。
- summary-only output，没有命令、日志、路径、artifact 或可复现引用。
- stale refs、间接 refs、不完整 refs。
- 只有 `output_ref` 但无法定位内容、hash、路径或生成上下文。
- 未经检查的假设、计划、自然语言解释。
- AI 对代码行为的描述但没有 source ref 或运行结果。

### 6.3 Unverified

以下情况标记为 Unverified：

- 没有 EvidenceRefs。
- 引用不可访问或超出当前授权边界。
- claim 无法在当前 workspace / logs / artifacts / docs 中检查。
- 证据与 claim 不匹配。

验收规则建议：重要 Chair 决策必须标注 `EvidenceStrength`。只有 Strong 可支持 `PASS`；Weak 或 Unverified 应导向 `NEEDS_MORE`、`Minimum Evidence Action` 或 `STOP_CONFIRM`。

## 7. 后端 API 建议

### 7.1 保持现有 API 兼容

- 不改变当前 `thread_runs`、`runs`、artifact API 的语义。
- 新字段全部按 optional 读取，写入端可渐进补充。
- 继续支持 artifact refs / evidence refs 为字符串。
- 不把 `run_id` 引入 checkpoint key 语义。

### 7.2 增量事件 API 形状

可在现有 run event/journal 上规范 payload，而不是立即新建表：

```json
{
  "event_id": "evt_...",
  "user_id": "...",
  "thread_id": "...",
  "run_id": "...",
  "task_id": null,
  "action_id": null,
  "round_id": null,
  "seq": 42,
  "created_at": "2026-07-04T00:00:00Z",
  "kind": "action.result",
  "surface": "task",
  "visible_in_chat": false,
  "payload": {},
  "provenance": {}
}
```

建议后端提供按用途过滤：

- `GET /api/threads/{thread_id}/events?surface=chat`：Thread timeline 展示。
- `GET /api/threads/{thread_id}/runs/{run_id}/events`：Run timeline 调试/任务面板。
- `GET /api/threads/{thread_id}/runs/{run_id}/tasks`：TaskLane 聚合视图。
- `GET /api/threads/{thread_id}/artifacts`：artifact index，兼容现有 artifact API。

以上是建议形状，不要求当前立即实现。

### 7.3 脱敏与审计

- SSE / journal 中默认不要持久化 raw prompt、raw message、raw result；保留 summary、hash、char count、refs。
- 对 SubagentFinding，重要原始输出优先落到授权范围内的 handoff file，再以 ref 传递。
- AuditSignal 只保存最小必要字段，不保存 secrets 或完整隐藏上下文。

## 8. 前端 state 分流建议

前端不要把所有 run events 塞进聊天消息数组。建议分为：

1. `chatMessages`: 仅 `visible_in_chat=true` 且 `surface=chat` 的消息。
2. `controlMessages`: summary、loop warning、handoff、round state 摘要。
3. `taskLanes`: 由 `task.event`、`action.result`、`subagent.finding` 聚合。
4. `artifactIndex`: artifact refs 与下载/预览状态。
5. `auditSignals`: evidence gap、boundary、permission、risk。
6. `roundState`: 当前 Command Room round 的 goal/boundary/evidence/next step。

展示策略：

- 默认聊天界面保持干净，只显示用户/助手对话与 Chair 摘要。
- Task 面板显示 lane 状态和可展开结果。
- Evidence/Audit 面板显示证据强弱、引用、缺口和 stop signals。
- Debug 模式才显示完整 Run timeline。

## 9. Migration 兼容建议

1. **只加不改**：新增字段 optional；旧事件按现有字段推导 `kind/surface/visible_in_chat`。
2. **字符串 ref 兼容**：`ArtifactRef`、`EvidenceRef` 同时接受 string 与 object。读取端统一 normalize。
3. **默认分类规则**：
   - 老聊天消息 -> `chat.message`, `surface=chat`, `visible_in_chat=true`。
   - 老 hidden/control message -> `control.message`, `visible_in_chat=false`。
   - 老 task terminal event -> `task.event` + 可选 `action.result`。
   - 老 artifact path -> string `ArtifactRef`，缺少 provenance 时标记 `provenance.redaction/strength` 为 unknown。
4. **不回填强语义**：没有硬证据的老 output 不应批量标为 Strong。
5. **checkpoint 不迁移**：本草案不改变 `thread_id + checkpoint_ns + checkpoint_id`。
6. **child conversation 延后**：若未来加入 child thread/run，需另起设计定义 owner、权限、checkpoint namespace、display contract、migration 和 rollback。

## 10. 非目标

本草案明确不做以下事情：

- 不要求马上落库或改 migration。
- 不改变生产权限默认值。
- 不定义新的跨 owner 分享语义。
- 不改变 artifact root/storage layout。
- 不改变 checkpoint keying 或 subagent checkpoint 隔离。
- 不让程序逻辑根据内容自动 PASS/FAIL。
- 不自动触发 reviewer、opposition、rework 或 governance loop。
- 不把 subagent 输出变成必须填写的表单。

## 11. 后续需要 Chair 决策的问题

1. 是否要把 `kind/surface/visible_in_chat/provenance` 作为所有 run event 的规范 envelope。
2. 是否需要单独 `round_id`，还是先把 RoundState 仅作为 payload 字段。
3. ArtifactRef 结构化到什么程度，何时从 string-only 进入 dual format。
4. EvidenceRef 是否需要 claim-level schema，还是先保持字符串 + strength。
5. 非 root checkpoint namespace 与 child conversation 是否进入下一阶段设计。

---

结论：建议第一阶段只把本协议作为运行时事件和证据分类的设计草案，优先指导 API payload、前端 state 分流和文档共识；不要立即推动数据库迁移或 checkpoint 语义变更。
