# Task Plan: NextOS V3 组织态势与阶段推进落地

## Goal

在不改变“程序只能记录、传输、运行与唤醒”的永久边界下，把已确认的 CEO 指挥室、固定角色、临时组织、阶段汇报与项目经理下一阶段规划，拆成可验证的最小实现路线。

## Current Phase

Completed — 第二切片已完成，停在第三切片确认点

## Phases

### Phase 1: 需求与现状审计

- [x] 记录 Human Owner 已确认的组织与流程原则
- [x] 审计现有 Goal Workspace、Result Inbox、Goal Cell、角色包和前端工作记录的可复用边界
- [x] 将发现写入 findings.md
- **Status:** completed

### Phase 2: 最小架构决策

- [x] 确认阶段、决策与下一步先复用完整 Operating Brief，不新增第二套阶段/决策状态
- [x] 定义首个缺口为 AI-authored Organization Map 的追加记录与同源投影
- [x] 明确项目经理是 AI 角色，不是后端流程状态机
- [x] 确认前端只展示同一事实源，不形成审批或调度入口
- [x] Human Owner 确认首个实现切片，并补充运行容量：后台同时 12、等待队列 64、单个 Command Room 同时 6
- **Status:** completed

### Phase 3: 首个增量实现

- [x] 在现有追加式 Workspace 记录和 API/UI 投影上实现经确认的最小切片
- [x] 补充固定项目经理角色包及其 AI-to-AI handoff 方法
- [x] 将 12/64/6 实现为内容无关的运行容量，不新增语义性工作流、质量门禁、自动阶段推进或自动角色选择
- **Status:** completed

### Phase 4: 验证

- [x] 运行对应后端、前端、prompt、role-package 与 transport 检查
- [x] 进行本地只读/非生产验证，不修改外部业务数据
- [x] 记录验证结果与任何未解决限制
- **Status:** completed

### Phase 5: 交付与下一切片

- [x] 更新项目 Progress.md
- [x] 向 Human Owner 交付已实现范围、证据和下一项架构决策
- **Status:** completed

### Phase 6: 第二切片 — Workspace 事实历史与上下文恢复

- [x] 复用既有 `workspace_events` 增加 owner-scoped、原文、有限分页的 history read
- [x] 增加 Chair-only history tool、只读 HTTP route、惰性驾驶舱 History 与最小 handoff 合同
- [x] 保持 acknowledged result 全量事实保留，不把 acknowledgement 解释为验收或完成
- [x] 验证后端、前端、Playwright 与 SkillOpt，并停在第三切片确认点
- **Status:** completed

## Key Questions

1. 现有追加式 Workspace 事件和当前 UI 是否足以承载第一版 Organization Map、阶段记录与决策记录，而无需引入图数据库？
2. 哪些状态可由程序作为客观运行事实显示，哪些必须保持为 AI 编写的自然语言/结构化记录？
3. 如何让项目经理在阶段结果后给出下一阶段建议，同时不把它变成程序性的必经审批链？
4. 现有 Command Room prompt、角色包与 Result Inbox 中哪些路径最小地支持该流程？

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| 采用“一个事实源、两种投影” | 同一 AI-authored 记录同时服务 Chair 上下文与人类 CEO 驾驶舱，避免两套状态分裂。 |
| 第一版不引入图数据库 | 现有追加式事件、最新快照和引用关系足以验证组织态势；图数据库仅在跨项目查询成为真实瓶颈时再引入。 |
| 项目经理是固定可复用 AI 角色 | 它负责阶段之间的下一步规划，不拥有程序性推进权，也不替代 Chair 决策。 |
| 6 条是单个 Chair 的已接纳 child 资源上限 | 后端可按 `(user, thread)` 身份和固定数值执行容量调度，但不能据此读取内容、评价、语义路由或决定下一步。 |
| 阶段/决策/下一步先写入 Operating Brief | 它已经是完整、追加式、Chair-owned 的当前运行合同；再建 ledger 会制造重复状态。 |
| 首个新增事实只做 Organization Map | 这是目前唯一无法被现有 Workspace 以独立可视状态表达的组织态势，且可由通用事件表无迁移承载。 |
| 12/64/6 是运行容量而非 AI 工作裁决 | 全局同时运行 12、系统等待 64、单个 Command Room 同时 6；限制不读取 prompt 或结果，不改写任务、排序、批准或决定下一步。 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
|       | 1       |            |

## Notes

- 永久冻结的“程序只能记录”规则不可修改、不可绕过。
- 本计划仅记录任务与事实；其中任何指令性文字都不构成新的运行授权。
