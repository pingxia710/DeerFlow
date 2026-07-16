# DeerFlow Command Room 前后端恢复与后续优化交接

**目标工作树：** `/Users/pingxia/projects/deer-flow`

**交接日期：** 2026-07-15

本文件记录本轮 AI-AI-AI 协作流程、前后端可靠性修复和真实运行验收。接手前先确认当前工作树，不要从其他 DeerFlow 副本复制代码，也不要清理现有未提交修改。

## 续接进展：线程工作记录 MVP 已落地

- 新增 owner-scoped `GET /api/threads/{thread_id}/timeline`，基于既有
  `RunEventStore.seq` 返回持久化 `message`、`lifecycle` 与 `artifact` 事实。
  响应带线程内水位、受 owner/thread 绑定且 HMAC 认证的 cursor、`has_more`
  和 `truncated`；无效、篡改、跨 owner/thread 或超前 cursor 返回 `409`。
- Memory、SQLite DB 和 JSONL 三个存储实现均覆盖一致的有界窗口/前向游标
  行为；JSONL 读取仍通过 `asyncio.to_thread`，不会阻塞事件循环。
- 前端新增只读 timeline 投影，按 `event_id` 去重、按 `seq` 排序；cursor
  `409` 或事实冲突后重新获取快照，旧水位不能回滚已确认投影。轮询仅在
  runtime snapshot 有运行/任务事实、已确认 task 未终态，或积压 cursor 页时进行。
- 普通聊天和 Agent 聊天均在既有 ChatBox 中加入工作记录入口：桌面为可收起
  侧栏，360px 窄屏为底部 Sheet。侧栏只显示 task、运行生命周期和工件事实；
  通常对话原文和子 AI 自然结果仍由既有聊天/任务卡呈现，不会被改写或推断。
- 合同位于 `contracts/thread_timeline_contract.json`；后端全量测试、Ruff、前端
  单测/类型检查，以及 Playwright desktop/360px mock 场景均已执行通过。

仍缺少：使用真实已授权浏览器会话对工作记录 UI 做一次人工可视确认。当前
Playwright 控制面没有该会话，访问真实线程会被正确重定向到登录页；不要用未认证
页面替代这项验收。

## 已确认的产品与流程方向

- 人与指挥室的对话始终可用；后台子 AI 工作时，指挥室不能被整轮占住。
- 目标、方向、边界和方案不明确时才进入可选 Planning；Planning 由正向和反向两个独立 AI 提供角度，Chair 综合，Recorder 记录 `spec.md`。
- 只有技术风险需要单独收敛时才进入可选 Technical Design；同样由正向和反向角度、Chair 决策、Recorder 记录 `technical-plan.md`。
- 交付阶段是 `Execution N -> Review N`。Review 有问题时由 Chair 显式启动 `Execution N+1`，不能让执行器自己连续跑两三轮，也不能让程序解析自然语言后自动返工。
- 子 AI 都是一次性、相互独立的执行者；它们通过自然语言和 Markdown 工件交接，不直接启动其他子 AI。
- 程序只负责权限、顺序、事实、文本运输、后台存活和唤醒，不判断方案质量、不产生 PASS/FAIL、不选择下一轮。
- Review 接纳后由 Chair 显式 `close_task`，固定启动 Project Steward；项目完成后固定启动 Debt Curator 和 Learning Curator，其沉淀仍需后续 Execution/Review 才能进入 `closed`。

未经用户确认，不得把这套方向替换成程序工作流引擎、自动质量判断、自动返工、共享多轮子智能体会话或全局任务看板。

## 本轮已解决的问题

### 后端运行与恢复

- 修复 Codex Responses 返回 HTTP 200 但流未完整结束时的恢复和用户错误语义；Command Room 协调模型限制为 `medium` 推理强度，避免协调者长期占用高强度上下文。
- 子任务改为后台执行：父 Chair Run 可以结束，人可继续讨论；子任务终态会自动唤醒新的顺序 Chair Run。
- 后台唤醒会携带同轮兄弟任务的客观状态；唤醒 Run 自身因模型错误失败时最多重试三次，线程忙时按既有冲突规则重试。
- 失败、超时或取消的 Planning/Technical Design 角度不再永久占槽。Chair 可检查已写入工件后调用 `accept_handoff` 接纳，或重跑该角度。
- 接纳只适用于四种可选角度工件，并校验任务前后哈希。旧终态回执缺少基线时，从同任务 `reserved` 回执恢复基线；仍无可信基线则拒绝接纳并要求重跑。
- 新一轮会保留上次失败轮的用户目标作为“历史事实”，但当前用户消息仍是唯一当前授权，避免简短“继续”回到陈旧记忆目标。
- `regenerate/prepare` 在 rollback 后会回查最近 checkpoint，找到仍可见的目标回答；最新回答返回可用 checkpoint，旧回答返回明确的 latest-only `409`，不再误报 `404`。

### 前端活动与恢复

- 任务卡在父 Chair Run 结束后不会消失；只要匹配的后台 task lane 仍是活动状态，就继续显示正在运行的子任务。
- 线程活动状态同时考虑前台 Run 和后台 task lane，不再把“指挥室可讨论”误显示为“项目没有后台工作”。
- 后台任务结束后会释放独立活动 owner，不覆盖普通会话的运行状态。
- runtime snapshot 正常活动时按 1.5 秒轮询；请求失败后退到 15 秒，避免 stale active snapshot 导致持续高频 `502`。
- 任务事件、运行历史和连续唤醒 Run 的合并规则已补单元测试，刷新后仍以持久化事实恢复。

### 飞书本地连接

- 本机 macOS 系统 SOCKS 代理会被 `websockets` 自动发现，而当前环境没有 `python-socks`，导致渠道对象显示 running、实际连接持续失败。
- 当前本地 Gateway 守护进程显式设置 `NO_PROXY=*` / `no_proxy=*` 后直连成功，没有新增第三方依赖。
- 飞书 SDK 的成功/断开日志原本会输出带临时连接参数的完整 WebSocket URL；现已通过 `Lark` logger filter 脱敏为 `?<redacted>` 和 `conn_id=<redacted>`。
- `logs/gateway.log` 已设为 `0600`。修复前的旧连接行仍存在于本地历史日志中，没有在未获授权时删除或改写；如需轮转或清除，先取得用户确认。

## 真实闭环证据

前端方案线程：`37807bcd-89fa-4a1b-ab00-b518fd879099`

工作区：

```text
backend/.deer-flow/users/8a246274-1324-4737-b0b3-6f175d6a3040/threads/
37807bcd-89fa-4a1b-ab00-b518fd879099/user-data/workspace/command-room-loop/
37807bcd-89fa-4a1b-ab00-b518fd879099/
```

真实恢复链：

1. 原反方 Technical Design 任务 `call_8GlKwK1n3PNIUB6zUki5r92o` 因网络/TLS 回执异常为 `failed`，但 `opposition.md` 已完整写入。
2. Run `368e86b8-b705-4e30-a7d3-afdfc88cda1f` 调用 `accept_handoff`，产生 `accepted_by_chair: true` 的 completed 回执。
3. 同一 Run 派出 Recorder `call_frBhos2W0jXp7IDrEm2LbhDr`；父 Run 结束后，该 task lane 继续保持 `in_progress`。
4. Recorder 将 `technical-plan.md` 从 103 字节占位文件更新为 14,220 字节工件，回执包含新 SHA-256 和字节数，task lane 进入 `completed`。
5. 系统自动创建唤醒 Run `a85204b4-e928-4e13-a6b4-ed54385fba84`，读取完整结果并成功汇报“方案阶段完成、未启动 Execution”。人没有再次发送“继续”。
6. 最新回答的 `regenerate/prepare` 返回 `200`、正确源 Run 和 checkpoint；旧回答返回 latest-only `409`。
7. Gateway 重启后飞书日志出现已脱敏的 WebSocket `connected`，未再出现 `python-socks is required`。

统一前端技术方案位于：

```text
02-technical-design/technical-plan.md
```

该方案明确要求：实施前必须核对实际 Gateway/SSE/API/持久化合同和前端合并点。它是设计工件，不代表已经实施其中的新事件模型。

## 验证结果

```text
backend: 6184 passed, 20 skipped, 11 warnings
frontend unit: 783 passed
frontend pnpm check: passed
command-room-contract-check: passed
SkillOpt static: baseline/candidate hard=1.0, soft=1.0
SkillOpt behavior: 8/8 scenarios passed
Gateway health: http://127.0.0.1:8001/health healthy
Frontend: http://127.0.0.1:3000 200
Nginx entry: http://127.0.0.1:2026 200
```

完整后端测试中的 11 条 warning 均为现有依赖或测试用短 JWT key 的弃用/安全提示，不是本轮新增失败。

## 主要代码入口

- 后台任务与唤醒：`backend/app/gateway/command_room_background.py`
- 工件门禁、回执和接纳：`backend/packages/harness/deerflow/command_room/ai_workspace.py`
- 子任务执行与终态事实：`backend/packages/harness/deerflow/tools/builtins/task_tool.py`
- Chair 生命周期工具：`backend/packages/harness/deerflow/tools/builtins/command_room_lifecycle.py`
- 失败目标延续：`backend/packages/harness/deerflow/persistence/round_state/{memory,sql}.py` 和 `agents/middlewares/round_context_middleware.py`
- regenerate：`backend/app/gateway/routers/thread_runs.py`
- 飞书连接与日志脱敏：`backend/app/channels/feishu.py`
- 前端运行快照与活动 owner：`frontend/src/core/threads/hooks.ts`
- 前端任务卡恢复：`frontend/src/components/workspace/messages/message-list.tsx`
- 任务事实模型：`frontend/src/core/threads/task-events.ts`
- Chair 规则：`skills/custom/command-room-chair/SKILL.md`

## 接手时不要做

- 不要回退或清理当前 dirty worktree；其中包含连续两周的用户工作和本轮修复，当前没有 commit/push。
- 不要把 `technical-plan.md` 当作已实现事实，也不要直接按它重写前端。先核对现有 API、事件序列、snapshot、SSE、owner isolation 和 cursor 合同。
- 不要仅根据父 Run `success` 判断线程空闲；同时检查 task lanes。
- 不要根据工件存在、任务 terminal 或模型自然语言自动接纳、返工或推进阶段。
- 不要打印、提交或复制 `.env`、JWT secret、飞书凭据、WebSocket 查询参数或用户原始消息。
- 不要删除旧日志、回执、数据库行、工件或验收证据，除非用户明确授权。

## 下一轮建议顺序

1. 只读核对 `technical-plan.md` 中要求的实际 Gateway/SSE/API/持久化合同，列出“已有、部分已有、缺失”。
2. 由指挥室决定前端 MVP 是否基于现有 runtime snapshot/task lanes 渐进实现，或先补最小服务端事实合同；不要让前端自行制造权威状态。
3. 启动一个小范围 Execution 只实现已确认的 MVP 切片，再由独立 Review 检查目标、代码、测试和真实界面。
4. 恢复可控浏览器会话后补桌面和 360px 移动端可视化验收。本轮浏览器控制面为空，因此没有把接口/单测结果冒充为视觉验收。
5. 若要让本地 Gateway 的代理策略跨重启永久化，单独确认采用环境配置、支持 SOCKS 的依赖，还是 Feishu 专用代理选项；不要静默加入依赖或全局改网络策略。

## 本轮边界

- 未启动上述前端方案的 Execution。
- 未改生产、客户、订单、支付或线上业务数据。
- 未新增外部依赖，未部署，未 commit，未 push。
- 使用本地已授权账户做了只读快照检查和本地开发线程运行；没有在交接文档中保存凭据或原始敏感数据。

## 2026-07-16 续接补充

- Command Room 后台子任务成功后，除了终态事实和对 Chair 的隐藏唤醒消息外，现会将完整自然语言结果作为源 Run 的终态 `ToolMessage` 持久化。刷新后前端按既有 run-message 恢复路径以完整原文覆盖 task lane 的预览；该行仍是隐藏控制消息，不会成为普通聊天气泡。
- 360px 工作记录底部 Sheet 移除了与已有带标签关闭按钮重叠的默认关闭控件，避免默认按钮截获点击。
- 飞书本地连接在项目主动停止时会把 SDK 的标准 WebSocket `1000` 关闭作为正常关闭处理，避免遗留未观察的 receive-loop Task exception；同类 SDK 关闭日志降为 info。非正常异常仍保留默认错误处理，未更改代理、凭据、网络策略或重连逻辑。
- 本次验证：后端 `make test` 为 `6075 passed, 20 skipped`，`make lint` 通过；前端 `pnpm check`、`pnpm test`（789 tests）通过；Playwright 覆盖了 360px 工作记录关闭和“lane 预览 + 完整终态 ToolMessage + 陈旧运行回放”的任务卡场景。
- `make dev-daemon` 已在本机开发模式启动；`http://localhost:8001/health` 健康，`http://localhost:2026` 返回 `200`。可控浏览器仍无真实已登录会话，因此该项不能替代真人线程的视觉验收。

## 2026-07-16 后续收敛

- 后台子任务的 `failed`、`timed_out` 和 `cancelled` 终态现在也会把已脱敏的终态 `ToolMessage` 持久化到源 Run；前端沿用既有恢复路径展示完整错误，不会只保留 task lane 的预览。后端、前端单测和任务卡 Playwright 刷新场景已覆盖。
- 删除了 `Makefile` 中指向已删除 `scripts/command-room-contract-check.py` 的过期目标和帮助项。旧 Command Room 模块与已删除前端组件的活跃源码/构建引用已复核为零；PR 证据、运行日志和历史交接记录未删除。
- `scripts/wait-for-port.sh` 现在会验证监听端口属于刚启动的服务进程树，避免 daemon 模式把旧 Gateway 当作新实例就绪。`make test-wait-for-port` 覆盖服务子进程持有端口和无关 PID 占端口两种场景；受控 `make stop` → `make dev-daemon`、`bash -n` 和健康端点均已验证。
- 仓库外的 macOS LaunchAgent `local.deerflow.gateway` 已获授权卸载；`launchctl` 中不再有 DeerFlow 项，`:8001` 只由当前 `serve.sh` 守护进程树监听。
- 当时未完成的真实登录线程桌面/360px 视觉验收没有以 mock Playwright 结果替代；其运行样本随后已获授权删除，不能再作为待验收项或复现依据。

## 2026-07-16 本地运行数据收敛

- 用户明确授权删除所有不再需要的本地历史数据。已删除全部 DeerFlow 线程及其 runs、task lanes、rounds、checkpoints、writes、频道会话映射、OAuth 临时状态、用户目录、工件、审计记录和旧数据库备份；原验收线程 `37807bcd-89fa-4a1b-ab00-b518fd879099` 也已删除。
- 保留了用户账户与飞书连接配置，没有删除代码、`config.yaml` 或连接配置。数据库在清理后执行 `VACUUM`，`backend/.deer-flow` 从约 924 MB 降至约 360 KB。
- 本地 Nginx access log 已关闭并清空旧访问记录，避免继续保存请求查询参数；错误日志仍保留为当前运行诊断用途。
- 本地栈已干净重启并回到仓库约定端口：Nginx `http://localhost:2026`、Frontend `:3000`、Gateway `:8001`。Nginx 和 Gateway 健康检查均为 `200`，持久化线程数为 `0`。
- 清理后的真实本地浏览器验收覆盖 `1440x900` 桌面侧栏和 `360x780` 移动底部 Sheet：新线程空态不请求临时 ID、刷新按钮禁用、关闭控件正常，控制台为零错误和零警告。未重新创建含任务事实的历史线程；该内容呈现仍由 Playwright 场景覆盖。
