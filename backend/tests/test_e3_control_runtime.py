from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import e3_control_runtime as e3
import pytest
from fastapi import HTTPException
from langchain_core.messages import HumanMessage

_RUNNER_SPEC = importlib.util.spec_from_file_location("run_wp1_e3_test_subject", Path(__file__).parents[1] / "scripts" / "run_wp1_e3.py")
assert _RUNNER_SPEC is not None and _RUNNER_SPEC.loader is not None
run_wp1_e3 = importlib.util.module_from_spec(_RUNNER_SPEC)
sys.modules[_RUNNER_SPEC.name] = run_wp1_e3
_RUNNER_SPEC.loader.exec_module(run_wp1_e3)


def _env(root: Path, **overrides: str) -> dict[str, str]:
    values = {
        "DEERFLOW_E3_TEST": "1",
        "DEERFLOW_E3_ROOT": str(root),
        "DEER_FLOW_HOME": str(root / "home"),
        "DEERFLOW_E3_MODE": "wf",
        "DEERFLOW_E3_BIND_HOST": "127.0.0.1",
    }
    values.update(overrides)
    return values


def test_validate_mount_requires_test_flag_loopback_and_private_home(tmp_path: Path):
    root = tmp_path / "e3"
    (root / "home").mkdir(parents=True)

    assert e3.validate_mount(environ=_env(root), host="127.0.0.1") == (root.resolve(), "wf")
    with pytest.raises(RuntimeError, match="DEERFLOW_E3_TEST"):
        e3.validate_mount(environ=_env(root, DEERFLOW_E3_TEST="0"), host="127.0.0.1")
    with pytest.raises(RuntimeError, match="loopback"):
        e3.validate_mount(environ=_env(root), host="0.0.0.0")
    with pytest.raises(RuntimeError, match="DEER_FLOW_HOME"):
        e3.validate_mount(environ=_env(root, DEER_FLOW_HOME=str(tmp_path)), host="127.0.0.1")


def test_public_evidence_redaction_never_keeps_wake_internals():
    assert e3.redact_public({"wake_state": "failed", "last_status": "http_503", "handoff": {"result": "secret"}}) == {"wake_state": "failed"}


def test_e3_chair_model_accepts_langchain_run_manager_positionally():
    result = e3.E3ChairModel()._generate([HumanMessage(content="E3_CHILD_regression")], None, None)

    assert result.generations[0].message.tool_calls[0]["name"] == "task"


def test_e3_chair_model_acks_background_result_by_message_name(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEERFLOW_E3_NONCE", "wake-nonce")

    result = e3.E3ChairModel()._generate([HumanMessage(content="[Internal Command Room background completion]", name="command_room_background_result")])

    assert result.generations[0].message.content == "E3_WAKE_ACK_wake-nonce"


def test_e3_chair_model_does_not_ack_background_result_by_content():
    result = e3.E3ChairModel()._generate([HumanMessage(content="command_room_background_result")])

    assert result.generations[0].message.content == "E3 metadata."


def test_wf_failure_seam_is_deterministic_and_does_not_seed_a_terminal_lane(tmp_path: Path):
    class App:
        class State:
            pass

        state = State()

        def include_router(self, _router):
            return None

    from app.gateway import command_room_background as background

    root = tmp_path / "e3"
    root.mkdir()
    original = background._start_wake_run
    try:
        e3.mount_controls(App(), root=root, mode="wf")
        with pytest.raises(HTTPException) as raised:
            asyncio.run(background._start_wake_run(None, None, None))
        assert raised.value.status_code == 503
    finally:
        background._start_wake_run = original


def test_child_command_paths_are_only_parsed_as_boolean_facts(tmp_path: Path):
    root = tmp_path / "e3"
    child = root / "workspace"
    child.mkdir(parents=True)
    assert e3._command_paths(("codex", "--cd", str(child), "--add-dir", str(root / "outputs"))) == [
        str(child),
        str(root / "outputs"),
    ]
    assert not e3._within("/tmp/not-e3", root)


def test_wf_controls_observe_the_temporary_owner_lane(tmp_path: Path):
    owner = "temporary-owner"
    job = {"thread_id": "thread", "run_id": "run", "task_id": "task", "user_id": owner, "round_id": "round"}
    lane = {"handoff": {"background_recovery": {"wake": {"state": "failed", "attempts": 3}}}}

    class Store:
        calls: list[dict[str, str]] = []

        async def get_task_lane(self, **kwargs):
            self.calls.append(kwargs)
            return lane if kwargs.get("user_id") == owner else None

    state = e3.E3ControlState(root=tmp_path, mode="wf", wf_jobs={"nonce": job})
    store = Store()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(round_state_store=store)),
        state=SimpleNamespace(user=SimpleNamespace(id=owner)),
        headers={},
    )
    endpoints = {route.path: route.endpoint for route in e3._router(state).routes}

    async def exercise() -> None:
        status = await endpoints["/api/test-only/e3/wf/status/{nonce}"]("nonce", request)
        assert status["wake_failed"] is True
        assert status["wake_attempts"] == 3
        await endpoints["/api/test-only/e3/wf/arm-zero-write/{nonce}"]("nonce", request)
        assert await endpoints["/api/test-only/e3/wf/zero-write/{nonce}"]("nonce", request) == {
            "mutator_calls": 0,
            "lane_digest_unchanged": True,
        }

    asyncio.run(exercise())
    assert store.calls == [{**{key: job[key] for key in ("thread_id", "run_id", "task_id")}, "user_id": owner}] * 3
    assert state.lane_digest_before == e3._digest(lane)


def test_e3_runner_defaults_to_wf_without_starting_r(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls: list[str] = []
    monkeypatch.setattr(run_wp1_e3, "_run_wf", lambda scenario: calls.append(scenario.name) is None)
    monkeypatch.setattr(run_wp1_e3, "_run_r", lambda _scenario: pytest.fail("E3-R must require explicit --scenario"))
    monkeypatch.setattr(run_wp1_e3, "_cleanup", lambda _scenario, *, passed: passed)
    monkeypatch.setattr(sys, "argv", ["run_wp1_e3.py", "--evidence-dir", str(tmp_path)])

    assert run_wp1_e3.main() == 0
    assert calls == ["wf"]
