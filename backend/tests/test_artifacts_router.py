import asyncio
import zipfile
from pathlib import Path

import pytest
from _router_auth_helpers import call_unwrapped, make_authed_test_app
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import FileResponse

import app.gateway.routers.artifacts as artifacts_router
from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, INTERNAL_SYSTEM_ROLE

ACTIVE_ARTIFACT_CASES = [
    ("poc.html", "<html><body><script>alert('xss')</script></body></html>"),
    ("page.xhtml", '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>hello</body></html>'),
    ("image.svg", '<svg xmlns="http://www.w3.org/2000/svg"><script>alert("xss")</script></svg>'),
]


def _make_request(query_string: bytes = b"") -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": query_string})


def test_get_artifact_reads_utf8_text_file_on_windows_locale(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "note.txt"
    text = "Curly quotes: \u201cutf8\u201d"
    artifact_path.write_text(text, encoding="utf-8")

    original_read_text = Path.read_text

    def read_text_with_gbk_default(self, *args, **kwargs):
        kwargs.setdefault("encoding", "gbk")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text_with_gbk_default)
    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: artifact_path)

    request = _make_request()
    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/note.txt", request))

    assert bytes(response.body).decode("utf-8") == text
    assert response.media_type == "text/plain"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_get_artifact_uses_trusted_internal_owner_header(tmp_path, monkeypatch) -> None:
    import deerflow.config.paths as paths_mod

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_mod, "_paths", None)

    owner_outputs = tmp_path / "users" / "owner-artifact" / "threads" / "thread-owned" / "user-data" / "outputs"
    owner_outputs.mkdir(parents=True)
    (owner_outputs / "owner.txt").write_text("owner artifact", encoding="utf-8")

    default_outputs = tmp_path / "users" / "default" / "threads" / "thread-owned" / "user-data" / "outputs"
    default_outputs.mkdir(parents=True)
    (default_outputs / "owner.txt").write_text("default artifact", encoding="utf-8")

    request = _make_request()
    request.state.user = type("InternalUser", (), {"id": "default", "system_role": INTERNAL_SYSTEM_ROLE})()
    request.scope["headers"] = [(INTERNAL_OWNER_USER_ID_HEADER_NAME.lower().encode(), b"owner-artifact")]

    response = asyncio.run(
        call_unwrapped(
            artifacts_router.get_artifact,
            "thread-owned",
            "mnt/user-data/outputs/owner.txt",
            request,
        )
    )

    assert bytes(response.body).decode("utf-8") == "owner artifact"


def test_get_artifact_uses_request_user_bucket(tmp_path, monkeypatch) -> None:
    import deerflow.config.paths as paths_mod

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_mod, "_paths", None)

    alice_outputs = tmp_path / "users" / "alice" / "threads" / "shared-thread" / "user-data" / "outputs"
    alice_outputs.mkdir(parents=True)
    (alice_outputs / "report.txt").write_text("alice artifact", encoding="utf-8")

    bob_outputs = tmp_path / "users" / "bob" / "threads" / "shared-thread" / "user-data" / "outputs"
    bob_outputs.mkdir(parents=True)
    (bob_outputs / "report.txt").write_text("bob artifact", encoding="utf-8")

    request = _make_request()
    request.state.user = type("User", (), {"id": "alice", "system_role": "user"})()

    response = asyncio.run(
        call_unwrapped(
            artifacts_router.get_artifact,
            "shared-thread",
            "mnt/user-data/outputs/report.txt",
            request,
        )
    )

    assert bytes(response.body).decode("utf-8") == "alice artifact"


@pytest.mark.parametrize(("filename", "content"), ACTIVE_ARTIFACT_CASES)
def test_get_artifact_forces_download_for_active_content(tmp_path, monkeypatch, filename: str, content: str) -> None:
    artifact_path = tmp_path / filename
    artifact_path.write_text(content, encoding="utf-8")

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: artifact_path)

    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", f"mnt/user-data/outputs/{filename}", _make_request()))

    assert isinstance(response, FileResponse)
    assert response.headers.get("content-disposition", "").startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(("filename", "content"), ACTIVE_ARTIFACT_CASES)
def test_get_artifact_forces_download_for_active_content_in_skill_archive(tmp_path, monkeypatch, filename: str, content: str) -> None:
    skill_path = tmp_path / "sample.skill"
    with zipfile.ZipFile(skill_path, "w") as zip_ref:
        zip_ref.writestr(filename, content)

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: skill_path)

    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", f"mnt/user-data/outputs/sample.skill/{filename}", _make_request()))

    assert response.headers.get("content-disposition", "").startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert bytes(response.body) == content.encode("utf-8")


def test_get_artifact_forces_download_for_unknown_binary(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "blob.unknown"
    payload = b"\x00\x01\x02binary"
    artifact_path.write_bytes(payload)

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: artifact_path)

    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/blob.unknown", _make_request()))

    assert bytes(response.body) == payload
    assert response.media_type == "application/octet-stream"
    assert response.headers.get("content-disposition", "").startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_get_artifact_forces_download_for_unknown_binary_in_skill_archive(tmp_path, monkeypatch) -> None:
    payload = b"\x00\x01\x02binary"
    skill_path = tmp_path / "sample.skill"
    with zipfile.ZipFile(skill_path, "w") as zip_ref:
        zip_ref.writestr("blob.unknown", payload)

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: skill_path)

    response = asyncio.run(call_unwrapped(artifacts_router.get_artifact, "thread-1", "mnt/user-data/outputs/sample.skill/blob.unknown", _make_request()))

    assert bytes(response.body) == payload
    assert response.media_type == "application/octet-stream"
    assert response.headers.get("content-disposition", "").startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_get_artifact_download_false_does_not_force_attachment(tmp_path, monkeypatch) -> None:
    artifact_path = tmp_path / "note.txt"
    artifact_path.write_text("hello", encoding="utf-8")

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: artifact_path)

    app = make_authed_test_app()
    app.include_router(artifacts_router.router)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/artifacts/mnt/user-data/outputs/note.txt?download=false")

    assert response.status_code == 200
    assert response.text == "hello"
    assert "content-disposition" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


def test_get_artifact_download_true_forces_attachment_for_skill_archive(tmp_path, monkeypatch) -> None:
    skill_path = tmp_path / "sample.skill"
    with zipfile.ZipFile(skill_path, "w") as zip_ref:
        zip_ref.writestr("notes.txt", "hello")

    monkeypatch.setattr(artifacts_router, "resolve_thread_virtual_path", lambda _thread_id, _path, **_kwargs: skill_path)

    app = make_authed_test_app()
    app.include_router(artifacts_router.router)

    with TestClient(app) as client:
        response = client.get("/api/threads/thread-1/artifacts/mnt/user-data/outputs/sample.skill/notes.txt?download=true")

    assert response.status_code == 200
    assert response.text == "hello"
    assert response.headers.get("content-disposition", "").startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_skill_archive_preview_rejects_oversized_member_before_decompression(tmp_path) -> None:
    skill_path = tmp_path / "sample.skill"
    payload = b"A" * (artifacts_router.MAX_SKILL_ARCHIVE_MEMBER_BYTES + 1)
    with zipfile.ZipFile(skill_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zip_ref:
        zip_ref.writestr("SKILL.md", payload)

    assert skill_path.stat().st_size < artifacts_router.MAX_SKILL_ARCHIVE_MEMBER_BYTES

    with pytest.raises(HTTPException) as exc_info:
        artifacts_router._extract_file_from_skill_archive(skill_path, "SKILL.md")

    assert exc_info.value.status_code == 413
