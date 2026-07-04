# DeerFlow Self-Repair Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce the confirmed minimal self-repair safety boundary for Command Room when DeerFlow is modifying DeerFlow itself.

**Architecture:** Add a small command-room-only system prompt section that states the self-repair boundary and test for its presence. Keep the existing hard tool boundary: Command Room direct tools stay read-only, and write/bash work stays delegated. Update the project instructions to point future agents at the approved safety spec.

**Tech Stack:** Python backend, LangGraph lead-agent prompt assembly, pytest, Markdown project instructions.

## Global Constraints

- The self-repair boundary applies only when DeerFlow is modifying DeerFlow itself.
- Command Room for DeerFlow self-repair is read-only: project file reads, log reads, task decomposition, evidence review.
- Command Room must not write files, run shell commands, commit, push, deploy, change `main`, edit production configuration, or change secrets when the target project is DeerFlow itself.
- Executor may write only inside an isolated repair git worktree and run local validation commands.
- Executor must not commit, push, deploy, perform production writes, change `main`, edit `.env` or secrets, run destructive git commands, delete historical evidence, or perform broad cleanup unless the user explicitly authorizes that step.
- Other projects use their own project rules; normal projects allow local reads/writes/checks, and high-risk projects require explicit approval for production writes or sensitive disclosure.
- Do not add an audit system, dashboard, PR automation, automatic merge, automatic reviewer, SkillOpt gate, permission platform, or workflow engine in this phase.
- Implement in an isolated worktree at execution time. Do not mix in the current main checkout's unstaged runtime files.

---

## File Structure

- Modify `backend/packages/harness/deerflow/agents/lead_agent/prompt.py`: add a command-room-only self-repair safety prompt section and wire it into `SYSTEM_PROMPT_TEMPLATE`.
- Modify `backend/tests/test_lead_agent_prompt.py`: add focused prompt tests for the new command-room self-repair section.
- Modify `AGENTS.md`: add one repo-wide instruction pointer to the approved self-repair safety spec.
- Modify `backend/AGENTS.md`: add one backend/command-room instruction pointer to the approved self-repair safety spec.

---

### Task 1: Command Room Self-Repair Prompt Guard

**Files:**
- Modify: `backend/packages/harness/deerflow/agents/lead_agent/prompt.py`
- Test: `backend/tests/test_lead_agent_prompt.py`

**Interfaces:**
- Consumes: `apply_prompt_template(agent_name=..., subagent_enabled=..., app_config=...)`
- Produces: `_build_command_room_self_repair_section(agent_name: str | None) -> str`

- [ ] **Step 1: Write failing tests for the command-room-only self-repair section**

Add these tests to `backend/tests/test_lead_agent_prompt.py` after `test_build_self_update_section_empty_for_command_room`:

```python
def test_build_command_room_self_repair_section_empty_for_non_command_room():
    assert prompt_module._build_command_room_self_repair_section(None) == ""
    assert prompt_module._build_command_room_self_repair_section("builder") == ""


def test_build_command_room_self_repair_section_present_for_command_room():
    section = prompt_module._build_command_room_self_repair_section("command-room")

    assert "DEERFLOW SELF-REPAIR SAFETY" in section
    assert "only when DeerFlow is modifying DeerFlow itself" in section
    assert "Command Room must stay read-only" in section
    assert "isolated git worktree" in section
    assert "Other projects use their own project rules" in section
    assert "Do not add audit systems, dashboards, PR automation, automatic merges, automatic reviewers, SkillOpt gates" in section
```

Add this test near the existing command-room prompt tests in `backend/tests/test_lead_agent_prompt.py`:

```python
def test_command_room_prompt_includes_self_repair_safety(monkeypatch):
    explicit_config = SimpleNamespace(
        sandbox=SimpleNamespace(
            use="deerflow.sandbox.local:LocalSandboxProvider",
            allow_host_bash=False,
            mounts=[],
        ),
        subagents=SubagentsAppConfig(custom_agents={}),
        skills=SimpleNamespace(container_path="skills"),
        skill_evolution=SimpleNamespace(enabled=False),
        tool_search=SimpleNamespace(enabled=False),
        memory=SimpleNamespace(enabled=False, injection_enabled=True, max_injection_tokens=2000),
        acp_agents={},
    )

    monkeypatch.setattr(
        prompt_module,
        "get_or_new_skill_storage",
        lambda app_config=None: SimpleNamespace(load_skills=lambda enabled_only=True: []),
    )
    monkeypatch.setattr(prompt_module, "get_agent_soul", lambda agent_name=None: "")

    prompt = prompt_module.apply_prompt_template(
        subagent_enabled=True,
        agent_name="command-room",
        app_config=explicit_config,
    )

    assert "<command_room_self_repair>" in prompt
    assert "DEERFLOW SELF-REPAIR SAFETY" in prompt
    assert "Command Room must stay read-only" in prompt
    assert "Executor work must happen in an isolated git worktree" in prompt
    assert "External gatekeeper" in prompt
    assert "Other projects use their own project rules" in prompt
```

- [ ] **Step 2: Run the prompt tests and verify they fail**

Run:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_lead_agent_prompt.py -q
```

Expected: failure mentioning `AttributeError: module 'deerflow.agents.lead_agent.prompt' has no attribute '_build_command_room_self_repair_section'`.

- [ ] **Step 3: Add the minimal command-room-only prompt section**

In `backend/packages/harness/deerflow/agents/lead_agent/prompt.py`, add this function after `_build_self_update_section`:

```python
def _build_command_room_self_repair_section(agent_name: str | None) -> str:
    """Command-room-only boundary for DeerFlow modifying DeerFlow itself."""
    if agent_name != "command-room":
        return ""
    return """<command_room_self_repair>
**DEERFLOW SELF-REPAIR SAFETY**
This section applies only when DeerFlow is modifying DeerFlow itself.

- Command Room must stay read-only for DeerFlow self-repair: read project files, read logs, frame repair tasks, delegate execution, and review evidence.
- Command Room must not write files, run shell commands, commit, push, deploy, change `main`, edit production configuration, or change secrets when the target project is DeerFlow itself.
- Executor work must happen in an isolated git worktree, not in the running DeerFlow checkout.
- Executors may edit code and run local checks only inside that repair worktree.
- Executors must return changed files, key diff summary, validation commands and results, and unresolved risks.
- Command Room summarizes the evidence only. The user or Codex is the External gatekeeper that decides whether to merge into the main DeerFlow checkout.
- Other projects use their own project rules. Do not apply this strict DeerFlow self-repair boundary to ordinary external projects unless their local instructions require it.
- Do not add audit systems, dashboards, PR automation, automatic merges, automatic reviewers, SkillOpt gates, permission platforms, or workflow engines for this phase.
</command_room_self_repair>
"""
```

In `SYSTEM_PROMPT_TEMPLATE`, insert `{command_room_self_repair_section}` immediately after `{self_update_section}`:

```python
{soul}
{self_update_section}
{command_room_self_repair_section}
	<thinking_style>
```

In `apply_prompt_template`, compute the section after `clarification_priority` is computed:

```python
    command_room_self_repair_section = _build_command_room_self_repair_section(agent_name)
```

In the final `SYSTEM_PROMPT_TEMPLATE.format(...)` call, add:

```python
        command_room_self_repair_section=command_room_self_repair_section,
```

Update `test_system_prompt_template_preserves_placeholders` in `backend/tests/test_lead_agent_prompt.py` to include:

```python
        "{command_room_self_repair_section}",
```

- [ ] **Step 4: Run the prompt tests and verify they pass**

Run:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_lead_agent_prompt.py -q
```

Expected: all tests in `tests/test_lead_agent_prompt.py` pass.

- [ ] **Step 5: Run the existing command-room tool-boundary tests**

Run:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_lead_agent_model_resolution.py::test_command_room_allows_direct_file_read_tool_group_without_bash tests/test_lead_agent_model_resolution.py::test_make_lead_agent_command_room_uses_direct_file_read_tool_group_without_bash -q
```

Expected: both tests pass, confirming Command Room direct tools remain `["file:read"]` and direct MCP tools remain disabled.

- [ ] **Step 6: Commit Task 1**

Run:

```bash
git add backend/packages/harness/deerflow/agents/lead_agent/prompt.py backend/tests/test_lead_agent_prompt.py
git commit -m "fix(command-room): add self-repair safety prompt"
```

Expected: commit succeeds. If pre-commit formats files, re-stage only these two files and rerun the commit.

---

### Task 2: Project Instruction Anchors

**Files:**
- Modify: `AGENTS.md`
- Modify: `backend/AGENTS.md`
- Reference: `docs/superpowers/specs/2026-07-02-deerflow-self-repair-safety-design.md`

**Interfaces:**
- Consumes: approved spec at `docs/superpowers/specs/2026-07-02-deerflow-self-repair-safety-design.md`
- Produces: short instruction pointers for future agents

- [ ] **Step 1: Add repo-wide self-repair pointer**

In `AGENTS.md`, add this bullet under `## Cross-Cutting Conventions`, after the existing **Command Room round principle** bullet:

```markdown
- **DeerFlow self-repair safety** — when Command Room helps modify DeerFlow itself,
  use the minimal self-repair loop in
  [docs/superpowers/specs/2026-07-02-deerflow-self-repair-safety-design.md](docs/superpowers/specs/2026-07-02-deerflow-self-repair-safety-design.md):
  Command Room stays read-only, executors work in isolated git worktrees, and
  the user or Codex decides whether to merge into the main checkout. Do not
  apply this stricter DeerFlow self-repair boundary to ordinary external projects
  unless their local instructions require it.
```

- [ ] **Step 2: Add backend/command-room self-repair pointer**

In `backend/AGENTS.md`, add this bullet under `### Command Room AI collaboration readiness signals`, after the first Command Room overview bullet:

```markdown
- DeerFlow self-repair uses the minimal safety loop in
  `docs/superpowers/specs/2026-07-02-deerflow-self-repair-safety-design.md`:
  Command Room may diagnose and delegate but must stay read-only when DeerFlow is
  modifying DeerFlow itself; executors edit only isolated git worktrees and return
  evidence; the user or Codex decides whether to merge. This is not a rule for
  ordinary external projects unless their local instructions say so.
```

- [ ] **Step 3: Verify the instruction anchors**

Run:

```bash
rg -n "self-repair safety|2026-07-02-deerflow-self-repair-safety-design|isolated git worktrees" AGENTS.md backend/AGENTS.md docs/superpowers/specs/2026-07-02-deerflow-self-repair-safety-design.md
```

Expected: matches in `AGENTS.md`, `backend/AGENTS.md`, and the spec file.

- [ ] **Step 4: Run the minimal backend prompt regression checks again**

Run:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_lead_agent_prompt.py tests/test_lead_agent_model_resolution.py::test_command_room_allows_direct_file_read_tool_group_without_bash tests/test_lead_agent_model_resolution.py::test_make_lead_agent_command_room_uses_direct_file_read_tool_group_without_bash -q
```

Expected: selected tests pass.

- [ ] **Step 5: Commit Task 2**

Run:

```bash
git add AGENTS.md backend/AGENTS.md
git commit -m "docs(command-room): anchor self-repair safety"
```

Expected: commit succeeds with only `AGENTS.md` and `backend/AGENTS.md` staged.

---

## Final Validation

- [ ] **Step 1: Check for accidental unrelated staged files**

Run:

```bash
git status -sb
```

Expected: no staged files. Existing unrelated local runtime files may still be shown as unstaged or untracked; do not stage them.

- [ ] **Step 2: Run the final focused test set**

Run:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_lead_agent_prompt.py tests/test_lead_agent_model_resolution.py -q
```

Expected: both test files pass.

- [ ] **Step 3: Report implementation evidence**

Return:

```text
Changed files:
- backend/packages/harness/deerflow/agents/lead_agent/prompt.py
- backend/tests/test_lead_agent_prompt.py
- AGENTS.md
- backend/AGENTS.md

Validation:
- PYTHONPATH=. uv run pytest tests/test_lead_agent_prompt.py tests/test_lead_agent_model_resolution.py -q

Safety:
- No submit/push/deploy/main merge performed.
- No .env or secret files changed.
- Runtime ignored command-room SOUL/config files were not used as the only enforcement layer.
```
