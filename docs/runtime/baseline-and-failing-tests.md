# DeerFlow Git 基线与失败测试分组

## 当前 Git 基线

- 路径：`/Users/pingxia/projects/deer-flow`
- 当前分支：`main`
- HEAD：`5bb952098955cbec1af84082a898065994d6ac2c`
- HEAD 提交：`5bb95209 fix(runtime): scope jsonl run events by owner`
- 跟踪状态：`main...origin/main [ahead 111]`
- 工作区：`git diff --stat` 无输出，未见未提交工作区差异。
- `origin/main...HEAD`：265 files changed, 25070 insertions(+), 1635 deletions(-)。这是主要冲突风险：本地 main 已累积大量未推送提交，覆盖 runtime、gateway、frontend、command-room、sandbox/security、tests/docs 等。

最近 20 个提交集中在多用户/多会话主线相关区域：owner scope、thread id 复用防护、run artifact、stream recovery、thread runs query cache、sandbox host mount 安全、run-scoped thread messages 等。

## 相关 worktree / 分支风险

`git branch -vv` 显示大量已 checkout 的并行 worktree，包括：

- `feature/multi-conversation-command-room-runtime`
- `fix/owner-scope-consistency`
- `fix/runtime-reliability-guards`
- `fix/jsonl-event-owner-scope`
- `topic/run-scoped-thread-messages`
- `topic/runtime-ownership-timeline-tests`
- `topic/thread-delete-run-cleanup`
- 多个 frontend/runtime contract topic 分支

风险：后续开发若直接基于 `main` 或这些 topic 分支继续改同一模块，容易在 runtime ownership、thread/run API、frontend cache/history、sandbox/security 测试上重复或相互覆盖。建议先确认 111 个 ahead 提交的集成边界，再开新 worktree。

## 测试基线

没有在仓库中找到可复用的“26 failed”日志或 pytest cache。只读收集过程中执行了：

- `python -m pytest --collect-only -q`：收集到 5563 个后端测试。
- 针对多用户/多会话主线相关测试：`tests/test_run_manager.py tests/test_runs_api_endpoints.py tests/test_thread_run_messages_pagination.py tests/test_threads_router.py`，结果 `146 passed`。
- 后端全量 `python -m pytest -q --tb=short --disable-warnings`：结果 `60 failed, 5466 passed, 18 skipped, 12 warnings, 19 errors`，耗时约 154s。

> 注意：当前实测不是“26 failed”，而是 60 failed + 19 errors。失败主因大多指向当前本机配置 `sandbox.mounts host_path '/Users/pingxia' is unsafe by default`，会导致依赖 app config 的测试批量失败。未修改配置、未安装依赖、未清理缓存。

## 失败/错误分组与主线影响

### 1. 配置/沙箱安全校验导致的级联失败（高影响）

代表错误：`AppConfig` 校验拒绝 `sandbox.mounts host_path '/Users/pingxia'`，原因是 home root mount 默认不安全。

涉及：

- `tests/test_client.py::TestArtifactHardening::test_artifact_leading_slash_stripped`
- `tests/test_gateway_docs_toggle.py::*`
- `tests/test_memory_router.py::*`
- `tests/test_oidc_auth.py::*`
- `tests/test_subagent_prompt_security.py::*`
- `tests/test_task_tool_core_logic.py::*`
- `tests/test_client_live.py::*` 19 个 error

对多用户/多会话主线影响：高。虽然很多可能是本地配置污染而非代码回归，但它会遮蔽真实的 owner scope、artifact hardening、subagent/task event、live client 行为问题。后续 AI 开发若直接看红灯，会难以区分配置失败与逻辑失败。

### 2. Memory router / owner scope 路由（高影响）

失败包括：

- export/import/clear/create/delete/update memory routes
- bound owner header、生效用户 fallback、unsafe owner header sanitize
- destructive write scoping

对主线影响：高。memory owner 绑定和 destructive write scope 是多用户隔离核心之一；即便当前由配置加载触发，也应在干净测试配置下单独复测。

### 3. Task tool / subagent / command-room handoff（高影响）

失败集中在：

- unknown subagent、host bash disabled、tool groups、model override
- command-room Target Role advisory behavior
- parent/subagent skill allowlist 继承与交集
- task running/completed/failed/cancelled/timed_out events
- cleanup / cancellation / usage reporting

对主线影响：高。多会话 command-room 和 subagent 编排依赖这些契约；当前批量失败需要先排除 AppConfig 本地 mount 影响，再判断是否真实回归。

### 4. Gateway docs / OIDC / client live（中到高影响）

涉及 docs toggle、OIDC forwarded headers、live chat/stream/tool/upload/artifact/config/error resilience。

对主线影响：中到高。若真实失败会影响部署入口、认证入口和端到端客户端；但 live tests 受本地服务/配置影响大，当前更像环境阻断。

### 5. Run/thread 多会话核心专项（当前通过，低即时风险）

专项运行结果：

- `test_run_manager.py`
- `test_runs_api_endpoints.py`
- `test_thread_run_messages_pagination.py`
- `test_threads_router.py`

结果：`146 passed`。

对主线影响：正向信号。当前 HEAD 上 run manager、runs API、thread run messages pagination、threads router 的核心专项未复现失败。

## 建议下一步

1. 先固定测试环境：用测试专用 config 或临时环境变量避免读取当前危险 home root mount 配置；不要改生产/个人 `.env` 或输出 secret。
2. 在干净配置下重跑失败分组的最小集合，优先顺序：
   - `tests/test_memory_router.py`
   - `tests/test_task_tool_core_logic.py`
   - `tests/test_client.py::TestArtifactHardening`
   - `tests/test_gateway_docs_toggle.py`
   - `tests/test_oidc_auth.py`
3. 将“配置阻断型失败”和“真实逻辑失败”分开建 issue/任务，避免后续多 Agent 重复修同一红灯。
4. 处理 Git 风险前，先决定本地 `main` ahead 111 是否作为新基线；若不是，应从明确 topic/worktree 分支继续，避免覆盖现有 runtime/owner/thread/security 改动。

## 证据位置

- Git 命令完整输出：`/Users/pingxia/projects/deer-flow/backend/.deer-flow/users/963870b2-72d1-4f61-b0bc-5a46617b16b7/threads/3ba75b81-e1c9-4e43-9288-7cbff600fc4f/user-data/outputs/.tool-results/bash-ce6b395e4bbb.log`
- 后端全量 pytest 输出：`/Users/pingxia/projects/deer-flow/backend/.deer-flow/users/963870b2-72d1-4f61-b0bc-5a46617b16b7/threads/3ba75b81-e1c9-4e43-9288-7cbff600fc4f/user-data/outputs/.tool-results/backend-pytest-full.log`
- 失败摘要：`/Users/pingxia/projects/deer-flow/backend/.deer-flow/users/963870b2-72d1-4f61-b0bc-5a46617b16b7/threads/3ba75b81-e1c9-4e43-9288-7cbff600fc4f/user-data/outputs/.tool-results/backend-failures-summary.txt`
- 多会话专项 pytest 输出：`/Users/pingxia/projects/deer-flow/backend/.deer-flow/users/963870b2-72d1-4f61-b0bc-5a46617b16b7/threads/3ba75b81-e1c9-4e43-9288-7cbff600fc4f/user-data/outputs/.tool-results/backend-targeted-pytest.log`
