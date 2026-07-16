from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.gateway.routers.thread_runs import (
    _THREAD_TIMELINE_CATEGORIES,
    ThreadTimelineRecord,
    ThreadTimelineResponse,
    _decode_timeline_cursor,
    _encode_timeline_cursor,
)

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "thread_timeline_contract.json"


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_thread_timeline_contract_matches_gateway_models():
    contract = _contract()

    assert contract["route"] == "/api/threads/{thread_id}/timeline"
    assert contract["method"] == "GET"
    assert set(contract["categories"]) == set(_THREAD_TIMELINE_CATEGORIES)
    assert set(contract["record_required_fields"]).issubset(ThreadTimelineRecord.model_fields)
    assert set(contract["response_required_fields"]).issubset(ThreadTimelineResponse.model_fields)
    assert contract["query"]["limit"] == {"default": 100, "minimum": 1, "maximum": 500}


def test_thread_timeline_cursor_is_bound_to_its_owner_and_thread():
    cursor = _encode_timeline_cursor(
        thread_id="thread-1",
        user_id="owner-1",
        after_seq=42,
        signing_secret="test-secret",
    )

    assert (
        _decode_timeline_cursor(
            cursor,
            thread_id="thread-1",
            user_id="owner-1",
            signing_secret="test-secret",
        )
        == 42
    )
    with pytest.raises(ValueError):
        _decode_timeline_cursor(
            cursor,
            thread_id="thread-2",
            user_id="owner-1",
            signing_secret="test-secret",
        )
    with pytest.raises(ValueError):
        _decode_timeline_cursor(
            cursor,
            thread_id="thread-1",
            user_id="owner-2",
            signing_secret="test-secret",
        )
    with pytest.raises(ValueError):
        _decode_timeline_cursor(
            f"{cursor[:-1]}{'A' if cursor[-1] != 'A' else 'B'}",
            thread_id="thread-1",
            user_id="owner-1",
            signing_secret="test-secret",
        )
    with pytest.raises(ValueError):
        _decode_timeline_cursor(
            "not-a-cursor-é",
            thread_id="thread-1",
            user_id="owner-1",
            signing_secret="test-secret",
        )
