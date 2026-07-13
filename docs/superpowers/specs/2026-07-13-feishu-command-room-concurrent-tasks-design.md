# Feishu Command Room Concurrent Tasks Design

日期：2026-07-13

分支：`fix/channel-command-room-runtime`
状态：已确认，待实现

## 目标

在同一个飞书聊天中，用户可以发送至少三个互不相关的顶层任务并让它们并行执行；每个任务使用独立的 DeerFlow thread，并把结果回复到触发它的原消息下。用户在某个任务的飞书回复串中继续追问时，仍沿用该任务的上下文。

## 已确认事实

- `ChannelManager` 已经以 semaphore 管理入站任务，运行时上限为 5，足以同时执行三个任务。
- 飞书顶层消息正常会以自己的 `message_id` 作为 `topic_id`，从而创建独立 DeerFlow thread。
- 当前的“待澄清”兼容逻辑会把没有飞书线程关系的新顶层消息，接到同一用户最早的待澄清任务上。若该任务仍在运行，`multitask_strategy: reject` 会返回 busy。
- 这正是本次新顶层任务被错误复用旧 thread 的原因；不是并发槽位不足。

## 方案选型

- **方案 A（采纳）**：只调整 Command Room 的飞书路由。新的顶层消息始终保留自己的 `topic_id`；已有飞书回复关系仍使用已持久化的 topic 映射。复用现有并发和 thread 创建逻辑。
- 方案 B（不采纳）：把 manager 的全局并发从 5 改为 3。它影响所有通道，且不能修复新消息被归入旧 thread 的问题。
- 方案 C（不采纳）：新增任务队列或一套 per-task worker。现有 topic/thread 机制已满足需求，重复实现会增加状态和故障面。

## 路由约定

1. 对 Feishu Command Room，`root_id`、`parent_id`、`thread_id` 都为空的新消息是新任务：`topic_id = message_id`。
2. 含有已知飞书回复关系的消息仍按现有 store 映射回原 `topic_id`，因此在原任务回复串中追问会继续原 DeerFlow thread。
3. Command Room 不再把无回复关系的顶层消息作为“纯文本待澄清回答”消费。旧的 pending clarification 记录留到 TTL 过期即可，不得劫持新任务。
4. 非 Command Room Feishu 会话维持现有 plain-message clarification 兼容行为，避免扩大本次行为变化。

用户侧约定：需要继续某个任务时，在该任务的飞书回复串中发送消息；新的顶层消息就是新的、可并行的任务。

## 最小改动

只修改 `backend/app/channels/feishu.py` 的 pending-clarification 路由条件：当该 Feishu channel session 使用 `command-room` 时，不消费没有回复关系的新顶层消息的 pending clarification。

不新增队列、数据库字段、配置项、依赖或全局并发设置。

## 验证

新增一个解析器测试，覆盖 Command Room 同一聊天中存在 pending clarification 时：

1. 新顶层消息保留自己的 `topic_id`，且不消费 pending 项；
2. 既有普通 Feishu plain-message clarification 测试继续通过；
3. 既有 parent/root 已持久化映射测试继续通过。

完成后运行飞书解析器测试、相关 channel 测试、ruff 和 Command Room contract check。运行中的 Gateway task 不重启、不取消；待其结束后才安排本地运行版本的集成与受控验证。

## 成功标准

- 连续发送三个新的顶层飞书任务时，三者拥有不同 `topic_id` / DeerFlow thread，不再因旧任务 busy 被拒绝。
- 在某一任务的飞书回复串中追问时，仍命中该任务的 DeerFlow thread。
- 非 Command Room Feishu 的待澄清兼容行为不变。
