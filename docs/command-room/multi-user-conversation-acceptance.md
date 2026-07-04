# DeerFlow 多用户/多会话 AI 工作底座验收用例清单

> 范围：不改代码验收清单。用于验证“一个用户可同时推进多个会话/任务线，每条线拥有独立 thread/run/messages/artifacts/action_result；AI-AI 子任务、验收、反方、证据检查可持久化和回放；前端切换/刷新不丢沟通信息；聊天、task_event、action_result、worker findings 分流；主 AI 能看到当前 round 的事实、边界、证据、下一步”。
>
> 参考仓库资料：`docs/command-room/multi-conversation-runtime-contract.md`、`docs/runtime/run-lifecycle-consistency.md`、`docs/command-room/core-invariants.md`。未访问外部服务，未读取 secrets。

## P0：上线阻断级验收

### P0-01 Owner isolation：用户只能看到自己的 thread/run/message
- **场景**：两个用户 A/B 同时使用系统，A 的会话、运行、消息、事件、产物不得被 B 读取或订阅。
- **前置条件**：存在用户 A、用户 B；A 已创建 `thread_A1` 并产生 `run_A1/messages/artifacts/task_events/action_results`；B 已登录。
- **步骤**：
  1. B 调用 thread 列表接口。
  2. B 用 `thread_A1` 调用详情、messages、runs、run events、artifacts 查询接口。
  3. B 尝试订阅 A 的 run stream/SSE。
  4. B 尝试用 A 的 `run_id` 调用 wait/cancel/replay。
- **期望结果**：
  - B 的列表不包含 A 的 thread。
  - 所有跨 owner 访问返回 403 或 404，不返回对象标题、消息片段、artifact 路径、run 状态等侧信道信息。
  - SSE 不建立或立即以权限错误结束，不泄露历史帧。
  - wait/cancel/replay 不改变 A 的 run 状态。
- **建议测试层级**：API/e2e/security manual。

### P0-02 Owner isolation：ID 枚举与 Last-Event-ID 不泄露跨用户事件
- **场景**：攻击者猜测他人 `thread_id/run_id/event_id` 或复用他人的 `Last-Event-ID`。
- **前置条件**：A 有一个持续输出的 run；B 知道或猜到 A 的部分 ID。
- **步骤**：
  1. B 带 A 的 `thread_id/run_id` 请求 run timeline replay。
  2. B 带 A 的 `Last-Event-ID` 请求自己的或 A 的 stream。
  3. B 遍历相邻 event id/seq。
- **期望结果**：
  - 权限校验先于 replay 定位和事件读取。
  - 不返回 A 的事件数量、最新 seq、terminal_reason、artifact ref。
  - B 的 stream 不因外部 Last-Event-ID 跳过或混入 A 的事件。
- **建议测试层级**：API/security replay。

### P0-03 同一用户多会话并发：不同 thread 可同时运行且互不阻塞
- **场景**：用户 U 同时推进多个独立任务线。
- **前置条件**：U 已创建 `thread_1`、`thread_2`，每个 thread 都允许新建 run。
- **步骤**：
  1. 几乎同时在两个 thread 发起 `run_1`、`run_2`。
  2. 分别发送不同提示词，触发不同 task_event/action_result/artifact。
  3. 同时订阅两个 stream。
- **期望结果**：
  - 两个 run 都可进入 running，不因同一 owner 被全局串行化。
  - `thread_1` timeline 不出现 `thread_2` 的消息、事件、artifact。
  - stream 帧包含可区分的 `thread_id/run_id` 或由订阅上下文强约束。
  - 完成后两个 thread 的 latest run、messages、artifacts 均独立。
- **建议测试层级**：API/e2e。

### P0-04 同一 thread 活跃 run 排他：禁止同一 thread 两个 mutating run 同时写入
- **场景**：同一个 thread 被重复点击、双标签页提交或多 worker 同时接收请求。
- **前置条件**：用户 U 有 `thread_1`；系统支持 run 状态查询。
- **步骤**：
  1. 对同一 `thread_1` 并发发起两个新 run。
  2. 观察两个请求返回和持久化 run 状态。
  3. 检查消息、checkpoint、artifact 是否被双写。
- **期望结果**：
  - 至多一个 run 获得 active slot/进入 running。
  - 另一个请求明确返回 conflict/queued/rejected 中约定的一种，不静默创建活动写入者。
  - checkpoint 与 timeline 不出现两个 active run 交错改写同一 thread 当前状态。
  - 若实现队列，queued run 在前一 run terminal 后才可变为 running。
- **建议测试层级**：unit/API/concurrency e2e。

### P0-05 前端 stream 切换：A 会话运行时切到 B 再切回 A 不丢帧
- **场景**：用户在多个对话之间切换，后台 run 继续输出。
- **前置条件**：`thread_A/run_A` 正在持续输出；`thread_B` 存在历史消息。
- **步骤**：
  1. 打开 A 并订阅 stream，记录已收到最后 event id。
  2. 切换到 B，等待 A 继续产生事件。
  3. 切回 A，使用 Last-Event-ID 或持久化 replay 恢复。
- **期望结果**：
  - B 页面不显示 A 的增量帧。
  - 切回 A 后补齐离开期间产生的所有事件，无重复导致的 UI 双显。
  - A 的最终消息、task_event、action_result、worker finding 完整可见且分类正确。
- **建议测试层级**：e2e/replay/manual。

### P0-06 刷新 replay：页面刷新后可重建当前 thread/run UI
- **场景**：浏览器刷新、断网重连、前端进程重启。
- **前置条件**：一个 run 已产生普通聊天消息、task_event、action_result、worker finding、artifact，并处于 running 或 terminal。
- **步骤**：
  1. 记录刷新前 UI 展示。
  2. 刷新页面或清空内存状态后重新进入同一 thread。
  3. 调用 thread timeline、run timeline、artifact 查询和必要的 replay。
- **期望结果**：
  - UI 可从持久化数据重建，不依赖前端内存。
  - running run 可继续接收后续 stream；terminal run 显示完整终态。
  - 历史事件顺序稳定，不因刷新改变消息/事件相对顺序。
- **建议测试层级**：e2e/replay。

### P0-07 task_event/action_result/worker finding 与聊天消息分流
- **场景**：AI-AI 子任务产生生命周期事件、工具事实、worker 总结，不应混成普通聊天。
- **前置条件**：某 run 会触发至少一个子任务，产生 `task_event`、`action_result`、`worker finding` 和 assistant chat。
- **步骤**：
  1. 发起该 run。
  2. 查询原始事件存储和前端展示模型。
  3. 检查每类数据的 type、所属 thread/run/task、展示区域。
- **期望结果**：
  - 普通 user/assistant chat 进入对话流。
  - `task_event` 进入任务/控制事件流，携带 task 生命周期语义。
  - `action_result` 作为事实/工具结果，不被渲染成 worker 自述聊天。
  - `worker finding` 作为子 AI 发现/结论，可回放、可引用，但与主对话消息有清晰边界。
  - 查询 API 支持按类型过滤或返回可判别字段。
- **建议测试层级**：unit/API/e2e/replay。

### P0-08 AI-AI 子任务记录归属：parent thread + parent run + task_id
- **场景**：当前阶段子任务不引入 child thread，而归属于父 run。
- **前置条件**：run 中触发 planner/evidence/opposition/executor 等子任务。
- **步骤**：
  1. 触发多个子任务并完成。
  2. 查询 task_event、action_result、worker finding。
  3. 按 `thread_id/run_id/task_id` 回放单个子任务。
- **期望结果**：
  - 每条子任务记录都有父 `thread_id`、父 `run_id`、稳定 `task_id`。
  - 不要求存在 child thread；如实现 child thread，也必须有明确 parent provenance 和权限继承规则。
  - 单个 task 的事件可独立过滤、按序回放。
- **建议测试层级**：API/replay。

### P0-09 artifact provenance：产物必须可追溯到产生上下文
- **场景**：run 生成文件、报告、图片或其他 artifact，后续要验收来源和权限。
- **前置条件**：某 run 通过 action 或 worker 生成至少一个 artifact。
- **步骤**：
  1. 生成 artifact。
  2. 查询 artifact 列表与详情。
  3. 从 artifact 反查 producing thread/run/task/action/message。
  4. 尝试跨 owner 访问 artifact。
- **期望结果**：
  - artifact 至少包含 owner、thread_id、run_id、生成时间、引用路径/标识；建议包含 task_id、action_id、artifact_type、storage_root/trust boundary。
  - 前端显示产物时能展示或内部保留来源，支持验收回放。
  - 跨 owner 访问被拒绝；路径不能用于目录穿越或读取非授权文件。
  - 若当前仍为字符串 ref，必须能通过父事件/result 上下文恢复 provenance，不出现孤儿产物。
- **建议测试层级**：API/e2e/security manual。

### P0-10 terminal_reason：终态原因可持久化、可查询、不可被迟到写覆盖
- **场景**：run 可能成功、失败、取消、回滚、超时、权限中止、恢复失败。
- **前置条件**：可模拟不同终止路径。
- **步骤**：
  1. 分别触发 success、model/tool error、user cancel、rollback、timeout。
  2. 查询 run 状态和 terminal metadata。
  3. 在 terminal 后模拟迟到事件/迟到成功写入。
- **期望结果**：
  - `status` 与 `terminal_reason` 分离且语义明确。
  - terminal_reason 在 run 详情、timeline 末尾事件、replay 中一致。
  - terminal 后迟到 owner/worker 不能覆盖既有 terminal_reason、completed_at、最终消息。
- **建议测试层级**：unit/API/replay/concurrency。

### P0-11 checkpoint/run/thread 三层历史不可混淆
- **场景**：thread timeline、run timeline、checkpoint history 是三种不同历史。
- **前置条件**：同一 thread 已完成至少两个 run，每个 run 产生消息、事件、checkpoint。
- **步骤**：
  1. 查询 thread 级时间线。
  2. 查询指定 run 的时间线。
  3. 查询/回放 checkpoint 或执行 regenerate/rollback。
  4. 比较三者的 key 与展示含义。
- **期望结果**：
  - thread timeline 跨 run 展示用户可见会话历史。
  - run timeline 仅展示指定 `thread_id + run_id` 的执行事件、task_event、action_result、finding。
  - checkpoint 以 `thread_id + checkpoint_ns + checkpoint_id` 定位；不得假设 run_id 是 checkpoint 隔离键。
  - rollback/regenerate 明确说明作用于哪个 checkpoint，并不篡改历史 run timeline。
- **建议测试层级**：API/replay/e2e。

### P0-12 主 AI round 上下文：事实、边界、证据、下一步可恢复
- **场景**：主 AI 在下一轮需要看到当前 round 的关键上下文，而不是混乱的原始聊天。
- **前置条件**：一次 round 包含用户目标、边界/权限、证据标准、反方意见、worker findings、action_results、Chair next decision。
- **步骤**：
  1. 执行一轮包含多角色/多事件的任务。
  2. 结束后刷新或新开页面。
  3. 查询供主 AI 下一轮使用的上下文摘要/事件回放。
- **期望结果**：
  - 可恢复本 round 的 facts/action_result、boundaries、evidence refs/strength、worker findings、recommended next decision。
  - 原始 upstream AI output 仍可作为 handoff 输入，不被索引字段替代。
  - 主 AI 不需要从混杂聊天中猜测哪些是事实、哪些是建议、哪些是权限边界。
- **建议测试层级**：API/replay/manual。

### P0-13 权限/安全边界：敏感资源和 secrets 不进入可回放上下文
- **场景**：工具执行或 artifact 生成过程中可能接触环境变量、凭证、私有路径。
- **前置条件**：测试环境有模拟 secret；run 会执行可能输出环境/路径的命令。
- **步骤**：
  1. 触发工具命令，模拟 stdout/stderr 包含 secret-like 字符串。
  2. 查询 messages、action_result、task_event、artifacts、replay。
  3. 尝试通过 artifact ref 读取任意路径。
- **期望结果**：
  - secrets 不被持久化到普通消息、action_result、worker finding 或 artifact metadata。
  - 安全拦截/脱敏事件可被记录为 task_event/action_result，但不暴露原文。
  - artifact 访问限定在授权存储根内，不允许 `../`、绝对路径逃逸、符号链接逃逸。
- **建议测试层级**：unit/API/security manual。

### P0-14 取消/回滚不影响其他会话线
- **场景**：用户取消或回滚某个 run，同时其他 thread 仍在运行。
- **前置条件**：同一用户有 `thread_A/run_A`、`thread_B/run_B` 正在运行。
- **步骤**：
  1. 对 `run_A` 发起 cancel 或 rollback。
  2. 持续观察 `run_B` stream 和最终状态。
  3. 查询两个 thread 的 checkpoint 与 timeline。
- **期望结果**：
  - A 进入明确 terminal_reason（cancelled/rolled_back/rollback_failed 等）。
  - B 不被取消、不丢 stream、不被回滚 checkpoint。
  - A 的 rollback 只影响 A thread 当前状态，不删除/改写 A 的历史 run 事件。
- **建议测试层级**：API/e2e/concurrency。

## P1：重要体验与一致性验收

### P1-01 多标签页同一用户视图一致
- **场景**：用户在两个浏览器标签页打开同一 thread。
- **前置条件**：`thread_1` 可运行；两个标签页均已登录同一用户。
- **步骤**：
  1. 标签页 1 发起 run。
  2. 标签页 2 进入同一 thread 并订阅/查询。
  3. 标签页 2 尝试再次发起 run 或取消 run。
- **期望结果**：
  - 标签页 2 能看到 run running 状态和增量事件。
  - 再次发起 run 遵守同一 thread active-run 排他策略。
  - cancel 权限一致，取消结果在两个标签页同步。
- **建议测试层级**：e2e/manual。

### P1-02 run 列表与 thread 标题/最后消息按 owner 和时间正确聚合
- **场景**：会话列表展示多个 thread 的最近状态。
- **前置条件**：用户有多个 thread，每个 thread 多个 run，含成功/失败/取消。
- **步骤**：
  1. 查询 thread 列表。
  2. 检查每个 thread 的 latest message、latest run status、updated_at。
  3. 切换排序/分页。
- **期望结果**：
  - 只聚合当前 owner 数据。
  - latest 指标来源明确：用户可见聊天不被 task_event/action_result 冒充。
  - 分页稳定，无重复/漏项。
- **建议测试层级**：API/e2e。

### P1-03 run timeline 顺序稳定：seq/created_at/stream id 一致
- **场景**：同一 run 内事件密集产生。
- **前置条件**：可产生多条 chat delta、task_event、action_result。
- **步骤**：
  1. 执行 run 并记录实时 stream 顺序。
  2. run 结束后查询持久化 timeline。
  3. 使用 replay 从中间 event id 恢复。
- **期望结果**：
  - 实时顺序、持久化顺序、replay 顺序一致或有明确定义的稳定排序。
  - event id/seq 单调，能作为幂等去重依据。
  - 同 timestamp 事件不随机换序。
- **建议测试层级**：unit/API/replay。

### P1-04 worker finding 支持证据引用与反方/验收回放
- **场景**：Evidence/Opposition/Executor 的 finding 需要被 Chair 验收和后续追溯。
- **前置条件**：run 产生多个 worker finding，含 EvidenceRefs、EvidenceStrength、Boundary Status。
- **步骤**：
  1. 查询 worker findings。
  2. 从 finding 打开引用的 action_result/artifact/message。
  3. 刷新后再次回放。
- **期望结果**：
  - finding 保留原始 worker 输出或 handoff envelope，不只保留摘要。
  - EvidenceRefs 可解析到具体事件/产物；缺失时标记 unverified。
  - 反方意见不会覆盖证据意见，二者可并列展示。
- **建议测试层级**：API/replay/manual。

### P1-05 action_result 幂等与去重
- **场景**：stream 重连或工具重试导致同一 action_result 重复到达。
- **前置条件**：可模拟同一 action id/result id 重放。
- **步骤**：
  1. 产生 action_result。
  2. 重放相同 event id 或相同 result id。
  3. 前端刷新并查询持久化结果。
- **期望结果**：
  - 存储层或展示层幂等，不产生重复事实卡片。
  - 若确为重试的新结果，应有新的 attempt/result id 并显示关联。
- **建议测试层级**：unit/API/e2e。

### P1-06 artifact 与 action_result/message 的双向引用
- **场景**：用户从消息看到产物，也能从产物看到产生原因。
- **前置条件**：某 action_result 生成 artifact，并由 assistant message 引用。
- **步骤**：
  1. 打开 assistant message 中的 artifact。
  2. 查看 artifact metadata。
  3. 返回产生该 artifact 的 action_result/task_event。
- **期望结果**：
  - message -> artifact、artifact -> producing event 至少一条路径可达。
  - 删除/失效 artifact 时 message 显示明确不可用状态，不误导为成功产物。
- **建议测试层级**：API/e2e/manual。

### P1-07 terminal 后 UI 状态一致
- **场景**：run 结束后按钮、stream、wait、timeline 状态一致。
- **前置条件**：可触发多种 terminal_reason。
- **步骤**：
  1. 执行 run 到 terminal。
  2. 同时调用 wait、run detail、timeline replay。
  3. 检查前端按钮与提示。
- **期望结果**：
  - wait 返回与 run detail 相同 status/terminal_reason。
  - stream 收到结束事件后不再显示“生成中”。
  - cancel 按钮不可用或返回已终止语义。
- **建议测试层级**：API/e2e。

### P1-08 checkpoint rollback 可审计
- **场景**：用户回滚到某 checkpoint 后，需要知道发生了什么。
- **前置条件**：thread 有多个 checkpoint 和至少一个可回滚 run。
- **步骤**：
  1. 选择 checkpoint 执行 rollback。
  2. 查询 rollback run 的事件和 metadata。
  3. 查看 thread 当前状态与历史 timeline。
- **期望结果**：
  - 记录 rollback_requested_at、目标 checkpoint、恢复结果或 rollback_error。
  - 历史 messages/events 不被物理删除或伪装成从未发生。
  - 当前 thread 状态与选定 checkpoint 一致。
- **建议测试层级**：API/replay/manual。

### P1-09 前端分类展示规则稳定
- **场景**：不同事件类型在 UI 有清晰位置。
- **前置条件**：run 产生所有主要事件类型。
- **步骤**：
  1. 打开 thread UI。
  2. 切换“聊天/任务/事实/产物/worker findings”等视图或折叠区。
  3. 刷新后比较展示。
- **期望结果**：
  - 普通聊天是主对话；task_event 可折叠或在任务轨；action_result 是事实/工具结果；finding 是 AI-AI 结果；artifact 在产物区。
  - 刷新前后分类不变。
  - 未知事件类型有安全降级展示，不污染主聊天。
- **建议测试层级**：e2e/manual。

### P1-10 权限边界变更需要可见记录
- **场景**：任务要求扩大写入范围、访问外部系统、读取敏感数据。
- **前置条件**：存在需要用户授权的操作。
- **步骤**：
  1. 触发越界操作请求。
  2. 用户拒绝或批准。
  3. 查询 round 上下文和 timeline。
- **期望结果**：
  - 系统停止执行越界操作直到授权。
  - 授权/拒绝作为边界相关事件持久化。
  - 后续主 AI 上下文能看到当前 Boundary Status。
- **建议测试层级**：API/e2e/manual。

### P1-11 跨 worker/重启 replay 一致性（若部署多 worker）
- **场景**：SSE 所在 worker 重启或请求路由到另一 worker。
- **前置条件**：多 worker 或可模拟进程重启；run events 持久化。
- **步骤**：
  1. run 正在输出时断开 SSE。
  2. 重启原 worker 或切换路由。
  3. 使用 Last-Event-ID 重连。
- **期望结果**：
  - replay 来源为持久化事件或等价机制，不依赖已丢失内存 buffer。
  - terminal 事件可恢复，wait 不永久挂起。
- **建议测试层级**：replay/e2e/manual。

### P1-12 run active ownership fencing（若部署多 worker）
- **场景**：迟到 worker 在 lease 失效后继续写入。
- **前置条件**：可模拟 worker A 获得 active slot 后失联，worker B 恢复/接管。
- **步骤**：
  1. A 创建 running run 并停止 heartbeat。
  2. B 在 lease 过期后执行恢复或终止。
  3. A 恢复并尝试写 completion/checkpoint/stream frame。
- **期望结果**：
  - A 的迟到写入被 lease_token/generation 拦截。
  - B 的 terminal_reason 不被覆盖。
  - checkpoint 不被旧 generation 改写。
- **建议测试层级**：unit/API/concurrency manual。

## P2：增强、可维护性与边缘验收

### P2-01 空 thread 与无 run thread 展示
- **场景**：用户创建 thread 后尚未发消息或 run 创建失败。
- **前置条件**：存在空 thread、pending 后失败的 thread。
- **步骤**：打开列表和详情。
- **期望结果**：空状态清晰；不会显示其他 thread 的消息；可继续发起新 run。
- **建议测试层级**：e2e/manual。

### P2-02 长 run 大量事件分页与性能
- **场景**：run 产生大量 stream delta、task_event、action_result。
- **前置条件**：可构造 1k+ 事件 run。
- **步骤**：查询 timeline 分页、前端滚动、replay 中间页。
- **期望结果**：分页稳定；内存不明显膨胀；可按类型过滤；不会因大量 task_event 淹没聊天。
- **建议测试层级**：API/e2e/performance manual。

### P2-03 未知/新版事件类型兼容
- **场景**：后端新增事件类型，旧前端尚未识别。
- **前置条件**：可注入 unknown event type。
- **步骤**：查询与展示该事件。
- **期望结果**：保留原始事件；UI 安全降级到“未知事件/调试详情”；不崩溃、不混入普通聊天。
- **建议测试层级**：unit/e2e。

### P2-04 artifact 失效、移动或清理后的历史可解释
- **场景**：产物文件被清理或存储迁移。
- **前置条件**：历史 message/finding 引用一个已失效 artifact。
- **步骤**：打开历史 thread 与 artifact 链接。
- **期望结果**：显示 artifact unavailable/expired；保留 provenance；不抛 500；不尝试读取任意替代路径。
- **建议测试层级**：API/e2e/manual。

### P2-05 子任务失败不吞掉父 run 事实
- **场景**：某 worker 子任务失败，但其他 action_result 已产生。
- **前置条件**：run 中一个 task 失败、另一个 task 成功。
- **步骤**：执行 run 并查看 timeline/finding。
- **期望结果**：失败 task_event 带错误原因；成功 action_result/finding 仍可见；父 run 是否 terminal failure 由明确策略决定。
- **建议测试层级**：API/e2e。

### P2-06 用户重命名/删除 thread 的历史边界
- **场景**：用户管理多个会话。
- **前置条件**：thread 有多 run 和 artifacts。
- **步骤**：重命名 thread；如支持删除则删除/归档。
- **期望结果**：重命名不改变 run/checkpoint/artifact 归属；删除/归档遵守 owner 权限并定义 artifact 处理策略。
- **建议测试层级**：API/e2e/manual。

### P2-07 时间、时区与审计字段一致
- **场景**：跨地区或长时间运行。
- **前置条件**：事件跨分钟/小时产生。
- **步骤**：检查 created_at/updated_at/completed_at/lease timestamps。
- **期望结果**：使用统一时间标准；排序不依赖本地格式；前端显示可读但不影响 API 排序。
- **建议测试层级**：unit/API。

### P2-08 导出/分享时不越权
- **场景**：用户导出某 thread 的完整历史或 artifact 包。
- **前置条件**：thread 含 messages、events、findings、artifacts。
- **步骤**：执行导出；用其他 owner 访问导出链接或文件。
- **期望结果**：导出只包含当前 owner 授权内容；artifact provenance 保留；分享需要显式权限，不因知道链接即可访问。
- **建议测试层级**：API/security manual。

### P2-09 回放测试夹具可复现真实 UI
- **场景**：后续把验收项转为自动化 replay 用例。
- **前置条件**：有标准 run event fixture。
- **步骤**：用 fixture 重放 thread/run；截图或查询 UI 状态。
- **期望结果**：同一 fixture 多次 replay 得到相同分类、顺序和 terminal 显示；fixture 不需要真实外部服务。
- **建议测试层级**：replay/e2e。

### P2-10 观测与诊断字段足够定位混流问题
- **场景**：出现“消息串线/产物串线/事件重复”时需要排查。
- **前置条件**：任意复杂 run。
- **步骤**：查看 API 响应、日志或调试面板中的标识字段。
- **期望结果**：每条记录可见或可追踪 owner/thread/run/task/event/artifact id；错误响应包含 request id；不包含 secret。
- **建议测试层级**：manual/API。

## 最小通过标准建议

- **P0 全部通过**：才可认为多用户/多会话工作底座满足基本安全、隔离、持久化和回放要求。
- **P1 大部分通过且有明确遗留项**：才适合进入灰度或多人试用。
- **P2 用作回归和体验增强**：可逐步转为自动化 fixture、replay、e2e 与安全测试。

## 后续转测试用例时的字段建议

每个自动化用例建议固定记录并断言以下字段：

- 身份与权限：`owner_id/user_id/tenant_id`、auth context、expected 403/404。
- 历史定位：`thread_id`、`run_id`、`checkpoint_ns`、`checkpoint_id`。
- 子任务定位：`task_id`、role/worker name、parent `thread_id/run_id`。
- 事件定位：`event_id`、`seq`、`event_type`、`created_at`、`Last-Event-ID`。
- 结果定位：`action_id/action_result_id`、`artifact_id/ref`、provenance。
- 终态：`status`、`terminal_reason`、`completed_at`、rollback/cancel metadata。
- 展示分类：chat、task_event、action_result、worker_finding、artifact、unknown。
