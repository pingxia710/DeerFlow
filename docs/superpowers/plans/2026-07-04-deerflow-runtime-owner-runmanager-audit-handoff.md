# DeerFlow Runtime Owner/RunManager Audit Handoff

**目标工作树:** 当前 DeerFlow 仓库 checkout。

**不要使用:** 任何空的 DeerFlow 镜像仓库、归档目录或非当前 checkout。接手前先用 `git rev-parse --show-toplevel` 确认目标仓库。

本 handoff 供 Codex 或人工接手高风险后续任务。不要包含 secrets，不引用内部 prompt，不做夸张流程设计。

## 全局禁止项

- 不要在接手前顺手改生产代码、测试代码或 `Progress.md`；先拆小切片、确认边界。
- 不要触碰或重构以下范围，除非明确进入对应任务包且有独立审查：RunManager、run store、stream persistence、checkpoint、rollback、lease、权限模型、Target Role 自动链、skill loading policy。
- 不要把 owner/thread/run 语义改成隐式全局状态；所有跨用户路径必须显式携带并校验 `user_id`。
- 不要做大规模迁移、统一重命名或“顺手清理”。

## 已完成 / 不应重复做的机械项

本轮已经完成，后续不要重复开相同小修：

- 安全默认 docs/test 小修。
- 前端 task event parser 收紧。
- failed terminal 防倒退。

## 高风险任务包 A：Owner / checkpoint / thread_id 一致性

**目标:** 让 checkpoint、thread history、event rows、recovery 在 owner 维度保持一致，避免跨用户串线、误删、误恢复。

### 禁止项

- 不要把 checkpoint namespace 做成只按 `thread_id` 隔离。
- 不要新增可绕过 `user_id` 的 history/context 查询入口。
- 不要让 delete cascade 只依赖 thread/run id；必须保持 owner isolation。
- 不要在 recovery 中按裸 `thread_id` 分组。

### 推荐最小切片顺序

1. **只读审计入口:** 列出所有 checkpoint namespace、thread history、thread context、event row 写入、delete cascade、recovery 分组路径。
2. **checkpoint owner namespace:** 明确 checkpoint key/namespace 包含 owner；补最小单元测试证明不同 owner 同 thread_id 不互相读取。
3. **delete cascade owner isolation:** 删除 run/thread/event/checkpoint 时必须同时约束 owner；补跨 owner 同 thread_id 的防误删测试。
4. **显式 user_id 查询:** `get_thread_history` 与 `thread_context_usage` 等读取路径显式接收/传递 `user_id`，拒绝隐式默认。
5. **event row user_id invariant:** 写 event row 时确保 `user_id` 不为空且与 run/thread owner 一致；异常路径失败为显式错误。
6. **recovery 分组:** recovery 按 `(user_id, thread_id)` 分组，避免同 thread_id 跨用户合并。

### 必要测试

- 两个 user 使用相同 `thread_id`：checkpoint 读写互不污染。
- 删除 user A 的 thread/run 不删除 user B 同 `thread_id` 的 checkpoint/event/history。
- `get_thread_history` 和 `thread_context_usage` 缺少 `user_id` 时失败，传入正确 `user_id` 时只返回该 owner 数据。
- event row 写入时 `user_id` invariant：空值、错 owner、run/thread 不匹配均失败。
- recovery 输入含同 `thread_id` 不同 user：按 `(user_id, thread_id)` 产生独立恢复单元。

### 验收命令

按实际改动选择最小集合，至少包含相关单测和静态检查：

```bash
git diff --check
cd backend && PYTHONPATH=. uv run pytest <owner/checkpoint/thread/recovery related tests> -q
```

## 高风险任务包 B：RunManager 生命周期一致性

**目标:** 统一 run 生命周期状态语义，把 lease/CAS/cancel/terminal/timeout/rollback/event seq/recovery 接入主路径，避免双终态、状态倒退、重复恢复和事件乱序。

### 禁止项

- 不要绕过 RunManager 主路径直接写 terminal 状态。
- 不要引入没有 CAS/lease 保护的状态迁移。
- 不要把 cancel intent、timeout、rollback 混成同一种 terminal 语义。
- 不要让 terminal event 可重复追加且改变最终状态。
- 不要做一次性大重构；先建立状态模型和迁移表。

### 推荐最小切片顺序

1. **状态模型清单:** 写出当前状态、允许迁移、terminal 状态、非法倒退；先测试现状缺口。
2. **主路径统一:** 所有 run 状态变更经 RunManager 或单一状态迁移函数；直接 store 写入只保留内部实现。
3. **lease/CAS 接入:** 对 running/heartbeat/terminal 迁移加 lease 与 compare-and-swap；失败时返回可观测冲突。
4. **cancel intent:** cancel 先记录 intent，再由持有 lease 的执行路径收敛到 cancelled terminal；重复 cancel 幂等。
5. **terminal 幂等:** succeeded/failed/cancelled/timeout/rolled_back 等 terminal 只允许首次落地；重复同终态无副作用，异终态拒绝。
6. **timeout/rollback 语义:** 明确 timeout 是否触发 rollback、rollback 失败如何呈现；不要覆盖原始失败原因。
7. **event seq / terminal event / recovery:** event seq 单调；terminal event 与状态一致；recovery 只恢复非 terminal 且 lease 过期/可接管的 run。

### 必要测试

- 合法状态迁移表测试；非法倒退、异终态覆盖、重复 terminal 均被拦截或幂等。
- lease/CAS 冲突测试：两个 worker 竞争同 run，仅一个成功。
- cancel intent 测试：重复 cancel 幂等，running 路径观察 intent 后进入 cancelled。
- timeout 与 rollback 测试：timeout 不误标 succeeded；rollback 结果不覆盖原始 terminal 语义。
- event seq 测试：并发/恢复后事件序号单调，terminal event 只出现一次或严格幂等。
- recovery 测试：terminal run 不恢复；非 terminal 且 lease 过期的 run 可按规则恢复。

### 验收命令

按实际改动选择最小集合，至少包含 RunManager/run store/recovery 相关测试：

```bash
git diff --check
cd backend && PYTHONPATH=. uv run pytest <runmanager/run-store/recovery related tests> -q
```

## 本 handoff 验证

仅需验证本文档本身：

```bash
git diff --check -- docs/superpowers/plans/2026-07-04-deerflow-runtime-owner-runmanager-audit-handoff.md
```
