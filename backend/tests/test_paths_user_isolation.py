"""Tests for user-scoped path resolution in Paths."""

import os
from pathlib import Path

import pytest

from deerflow.config.paths import (
    Paths,
    UnsafePathError,
    validate_thread_id,
    validate_user_id,
)


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(tmp_path)


class TestValidateUserId:
    def test_valid_user_id(self, paths: Paths):
        d = paths.user_dir("u-abc-123")
        assert d == paths.base_dir / "users" / "u-abc-123"

    def test_rejects_path_traversal(self, paths: Paths):
        with pytest.raises(ValueError, match="Invalid user_id"):
            paths.user_dir("../escape")

    def test_rejects_slash(self, paths: Paths):
        with pytest.raises(ValueError, match="Invalid user_id"):
            paths.user_dir("foo/bar")

    def test_rejects_empty(self, paths: Paths):
        with pytest.raises(ValueError, match="Invalid user_id"):
            paths.user_dir("")

    def test_rejects_filesystem_component_over_255_utf8_bytes(self):
        with pytest.raises(ValueError, match="255 UTF-8 bytes"):
            validate_user_id("u" * 256)


class TestValidateThreadId:
    def test_accepts_255_byte_filesystem_component(self):
        assert validate_thread_id("t" * 255) == "t" * 255

    def test_rejects_filesystem_component_over_255_utf8_bytes(self):
        with pytest.raises(ValueError, match="255 UTF-8 bytes"):
            validate_thread_id("t" * 256)


class TestMakeSafeUserId:
    def test_already_safe_id_is_unchanged(self):
        from deerflow.config.paths import make_safe_user_id

        assert make_safe_user_id("ou_abc-123") == "ou_abc-123"
        assert make_safe_user_id("123456") == "123456"

    def test_unsafe_chars_are_sanitized_with_stable_suffix(self):
        from deerflow.config.paths import make_safe_user_id

        result = make_safe_user_id("user@example.com")
        # Sanitized prefix plus a stable digest of the original.
        assert result.startswith("user-example-com-")
        assert len(result.rsplit("-", 1)[1]) == 16
        assert result == "user-example-com-b4c9a289323b21a0"
        assert make_safe_user_id("user@example.com") == result

    def test_sanitized_id_passes_validation(self, paths: Paths):
        from deerflow.config.paths import make_safe_user_id

        safe = make_safe_user_id("用户/../etc")
        # Must be usable as a filesystem-scoped bucket without raising.
        assert paths.user_dir(safe) == paths.base_dir / "users" / safe

    def test_distinct_unsafe_ids_do_not_collide(self):
        from deerflow.config.paths import make_safe_user_id

        assert make_safe_user_id("a.b") != make_safe_user_id("a/b")

    @pytest.mark.parametrize("raw", ["u" * 256, "u" * 300 + "@example.com"])
    def test_long_external_id_is_normalized_to_valid_component(self, raw: str):
        from deerflow.config.paths import make_safe_user_id

        safe = make_safe_user_id(raw)

        assert len(safe.encode("utf-8")) <= 255
        assert validate_user_id(safe) == safe

    def test_empty_id_rejected(self):
        from deerflow.config.paths import make_safe_user_id

        with pytest.raises(ValueError, match="non-empty"):
            make_safe_user_id("")


class TestUserDir:
    def test_user_dir(self, paths: Paths):
        assert paths.user_dir("alice") == paths.base_dir / "users" / "alice"

    def test_prepare_user_dir_migrates_unique_legacy_unsafe_bucket(self, paths: Paths):
        from deerflow.config.paths import make_safe_user_id

        raw = "user@example.com"
        safe = make_safe_user_id(raw)
        legacy_dir = paths.base_dir / "users" / "user-example-com-63a710569261a24b"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "memory.json").write_text('{"legacy": true}\n', encoding="utf-8")

        assert paths.prepare_user_dir_for_raw_id(raw) == safe

        current_dir = paths.user_dir(safe)
        assert current_dir.exists()
        assert not legacy_dir.exists()
        assert (current_dir / "memory.json").read_text(encoding="utf-8") == '{"legacy": true}\n'

    def test_prepare_user_dir_never_migrates_another_users_bucket(self, paths: Paths):
        """A different raw ID with the same sanitized prefix has a different legacy digest."""
        import hashlib

        from deerflow.config.paths import make_safe_user_id

        users_dir = paths.base_dir / "users"
        other_legacy = users_dir / f"a-b-{hashlib.sha1(b'a/b').hexdigest()[:16]}"
        other_legacy.mkdir(parents=True)
        arbitrary_16_hex = users_dir / "a-b-1111111111111111"
        arbitrary_16_hex.mkdir(parents=True)

        assert paths.prepare_user_dir_for_raw_id("a.b") == make_safe_user_id("a.b")

        assert not paths.user_dir(make_safe_user_id("a.b")).exists()
        assert other_legacy.exists()
        assert arbitrary_16_hex.exists()


class TestUserMemoryFile:
    def test_user_memory_file(self, paths: Paths):
        assert paths.user_memory_file("bob") == paths.base_dir / "users" / "bob" / "memory.json"


class TestUserAgentMemoryFile:
    def test_user_agent_memory_file(self, paths: Paths):
        expected = paths.base_dir / "users" / "bob" / "agents" / "myagent" / "memory.json"
        assert paths.user_agent_memory_file("bob", "myagent") == expected

    def test_user_agent_memory_file_lowercases_name(self, paths: Paths):
        expected = paths.base_dir / "users" / "bob" / "agents" / "myagent" / "memory.json"
        assert paths.user_agent_memory_file("bob", "MyAgent") == expected


class TestUserAgentDir:
    def test_user_agents_dir(self, paths: Paths):
        assert paths.user_agents_dir("alice") == paths.base_dir / "users" / "alice" / "agents"

    def test_user_agent_dir(self, paths: Paths):
        assert paths.user_agent_dir("alice", "code-reviewer") == paths.base_dir / "users" / "alice" / "agents" / "code-reviewer"

    def test_user_agent_dir_lowercases_name(self, paths: Paths):
        assert paths.user_agent_dir("alice", "CodeReviewer") == paths.base_dir / "users" / "alice" / "agents" / "codereviewer"

    def test_user_agent_dir_validates_user_id(self, paths: Paths):
        with pytest.raises(ValueError, match="Invalid user_id"):
            paths.user_agent_dir("../escape", "myagent")


class TestUserThreadDir:
    def test_user_thread_dir(self, paths: Paths):
        expected = paths.base_dir / "users" / "u1" / "threads" / "t1"
        assert paths.thread_dir("t1", user_id="u1") == expected

    def test_thread_dir_no_user_id_falls_back_to_legacy(self, paths: Paths):
        expected = paths.base_dir / "threads" / "t1"
        assert paths.thread_dir("t1") == expected


class TestUserSandboxDirs:
    def test_sandbox_work_dir(self, paths: Paths):
        expected = paths.base_dir / "users" / "u1" / "threads" / "t1" / "user-data" / "workspace"
        assert paths.sandbox_work_dir("t1", user_id="u1") == expected

    def test_sandbox_uploads_dir(self, paths: Paths):
        expected = paths.base_dir / "users" / "u1" / "threads" / "t1" / "user-data" / "uploads"
        assert paths.sandbox_uploads_dir("t1", user_id="u1") == expected

    def test_sandbox_outputs_dir(self, paths: Paths):
        expected = paths.base_dir / "users" / "u1" / "threads" / "t1" / "user-data" / "outputs"
        assert paths.sandbox_outputs_dir("t1", user_id="u1") == expected

    def test_sandbox_user_data_dir(self, paths: Paths):
        expected = paths.base_dir / "users" / "u1" / "threads" / "t1" / "user-data"
        assert paths.sandbox_user_data_dir("t1", user_id="u1") == expected

    def test_acp_workspace_dir(self, paths: Paths):
        expected = paths.base_dir / "users" / "u1" / "threads" / "t1" / "acp-workspace"
        assert paths.acp_workspace_dir("t1", user_id="u1") == expected

    def test_legacy_sandbox_work_dir(self, paths: Paths):
        expected = paths.base_dir / "threads" / "t1" / "user-data" / "workspace"
        assert paths.sandbox_work_dir("t1") == expected


class TestHostPathsWithUserId:
    def test_host_thread_dir_with_user_id(self, paths: Paths):
        result = paths.host_thread_dir("t1", user_id="u1")
        assert "users" in result
        assert "u1" in result
        assert "threads" in result
        assert "t1" in result

    def test_host_thread_dir_legacy(self, paths: Paths):
        result = paths.host_thread_dir("t1")
        assert "threads" in result
        assert "t1" in result
        assert "users" not in result

    def test_host_sandbox_user_data_dir_with_user_id(self, paths: Paths):
        result = paths.host_sandbox_user_data_dir("t1", user_id="u1")
        assert "users" in result
        assert "user-data" in result

    def test_host_sandbox_work_dir_with_user_id(self, paths: Paths):
        result = paths.host_sandbox_work_dir("t1", user_id="u1")
        assert "workspace" in result

    def test_host_sandbox_uploads_dir_with_user_id(self, paths: Paths):
        result = paths.host_sandbox_uploads_dir("t1", user_id="u1")
        assert "uploads" in result

    def test_host_sandbox_outputs_dir_with_user_id(self, paths: Paths):
        result = paths.host_sandbox_outputs_dir("t1", user_id="u1")
        assert "outputs" in result

    def test_host_acp_workspace_dir_with_user_id(self, paths: Paths):
        result = paths.host_acp_workspace_dir("t1", user_id="u1")
        assert "acp-workspace" in result


class TestEnsureAndDeleteWithUserId:
    def test_ensure_thread_dirs_creates_user_scoped(self, paths: Paths):
        paths.ensure_thread_dirs("t1", user_id="u1")
        assert paths.sandbox_work_dir("t1", user_id="u1").is_dir()
        assert paths.sandbox_uploads_dir("t1", user_id="u1").is_dir()
        assert paths.sandbox_outputs_dir("t1", user_id="u1").is_dir()
        assert paths.acp_workspace_dir("t1", user_id="u1").is_dir()

    def test_concurrent_ensure_thread_dirs_is_idempotent(
        self,
        paths: Paths,
        monkeypatch,
    ):
        thread_id = "concurrent-thread"
        (paths.user_dir("u1") / "threads").mkdir(parents=True)
        original_mkdir = os.mkdir

        def competing_mkdir(path, mode=0o777, *, dir_fd=None):
            if path == thread_id:
                original_mkdir(path, mode, dir_fd=dir_fd)
                raise FileExistsError(17, "File exists", path)
            return original_mkdir(path, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "mkdir", competing_mkdir)
        paths.ensure_thread_dirs(thread_id, user_id="u1")

        assert paths.sandbox_uploads_dir(thread_id, user_id="u1").is_dir()

    def test_ensure_thread_dirs_rejects_symlinked_standard_directory(
        self,
        paths: Paths,
        tmp_path: Path,
    ):
        outside = tmp_path / "other-owner"
        outside.mkdir(mode=0o700)
        uploads_dir = paths.sandbox_uploads_dir("t1", user_id="u1")
        uploads_dir.parent.mkdir(parents=True)
        uploads_dir.symlink_to(outside, target_is_directory=True)

        with pytest.raises(UnsafePathError):
            paths.ensure_thread_dirs("t1", user_id="u1")

        assert outside.stat().st_mode & 0o777 == 0o700

    def test_delete_thread_dir_removes_user_scoped(self, paths: Paths):
        paths.ensure_thread_dirs("t1", user_id="u1")
        assert paths.thread_dir("t1", user_id="u1").exists()
        paths.delete_thread_dir("t1", user_id="u1")
        assert not paths.thread_dir("t1", user_id="u1").exists()

    def test_delete_thread_dir_idempotent(self, paths: Paths):
        paths.delete_thread_dir("nonexistent", user_id="u1")  # should not raise

    def test_ensure_thread_dirs_legacy_still_works(self, paths: Paths):
        paths.ensure_thread_dirs("t1")
        assert paths.sandbox_work_dir("t1").is_dir()

    def test_user_scoped_and_legacy_are_independent(self, paths: Paths):
        paths.ensure_thread_dirs("t1", user_id="u1")
        paths.ensure_thread_dirs("t1")
        # Both exist independently
        assert paths.thread_dir("t1", user_id="u1").exists()
        assert paths.thread_dir("t1").exists()
        # Delete one doesn't affect the other
        paths.delete_thread_dir("t1", user_id="u1")
        assert not paths.thread_dir("t1", user_id="u1").exists()
        assert paths.thread_dir("t1").exists()


class TestClaimLegacyThreadDirs:
    @pytest.mark.parametrize("candidate", ["legacy", "default", "owner"])
    def test_rejects_symlinked_thread_roots(
        self,
        paths: Paths,
        tmp_path: Path,
        candidate: str,
    ):
        thread_id = "thread-symlink"
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        candidates = {
            "legacy": paths.thread_dir(thread_id),
            "default": paths.thread_dir(thread_id, user_id="default"),
            "owner": paths.thread_dir(thread_id, user_id="owner-a"),
        }
        root = candidates[candidate]
        root.parent.mkdir(parents=True, exist_ok=True)
        root.symlink_to(outside, target_is_directory=True)

        with pytest.raises(ValueError, match="symlink"):
            paths.claim_legacy_thread_dirs(thread_id, "owner-a")

        assert root.is_symlink()
        assert (outside / "secret.txt").read_text(encoding="utf-8") == "secret"

    def test_rejects_symlink_inside_legacy_thread_tree(
        self,
        paths: Paths,
        tmp_path: Path,
    ):
        thread_id = "thread-nested-symlink"
        outside = tmp_path / "foreign-user-data"
        outside.mkdir()
        (outside / "secret.txt").write_text("foreign secret", encoding="utf-8")
        legacy = paths.thread_dir(thread_id)
        legacy.mkdir(parents=True)
        (legacy / "user-data").symlink_to(outside, target_is_directory=True)

        with pytest.raises(ValueError, match="symlink"):
            paths.claim_legacy_thread_dirs(thread_id, "owner-a")

        assert legacy.exists()
        assert not paths.thread_dir(thread_id, user_id="owner-a").exists()
        assert (outside / "secret.txt").read_text(encoding="utf-8") == "foreign secret"


class TestResolveVirtualPathWithUserId:
    def test_resolve_virtual_path_with_user_id(self, paths: Paths):
        paths.ensure_thread_dirs("t1", user_id="u1")
        result = paths.resolve_virtual_path("t1", "/mnt/user-data/workspace/file.txt", user_id="u1")
        expected_base = paths.sandbox_user_data_dir("t1", user_id="u1").resolve()
        assert str(result).startswith(str(expected_base))

    def test_resolve_virtual_path_legacy(self, paths: Paths):
        paths.ensure_thread_dirs("t1")
        result = paths.resolve_virtual_path("t1", "/mnt/user-data/workspace/file.txt")
        expected_base = paths.sandbox_user_data_dir("t1").resolve()
        assert str(result).startswith(str(expected_base))
