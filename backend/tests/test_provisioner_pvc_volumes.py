"""Regression tests for provisioner PVC volume support."""

import inspect

# ── _build_volumes ─────────────────────────────────────────────────────


class TestBuildVolumes:
    """Tests for _build_volumes: PVC vs hostPath selection."""

    def test_default_uses_hostpath_for_skills(self, provisioner_module):
        """When SKILLS_PVC_NAME is empty, skills volume should use hostPath."""
        provisioner_module.SKILLS_PVC_NAME = ""
        volumes = provisioner_module._build_volumes("thread-1")
        skills_vol = volumes[0]
        assert skills_vol.host_path is not None
        assert skills_vol.host_path.path == provisioner_module.SKILLS_HOST_PATH
        assert skills_vol.host_path.type == "Directory"
        assert skills_vol.persistent_volume_claim is None

    def test_default_uses_hostpath_for_userdata(self, provisioner_module):
        """When USERDATA_PVC_NAME is empty, user-data volume should use hostPath."""
        provisioner_module.USERDATA_PVC_NAME = ""
        volumes = provisioner_module._build_volumes("thread-1")
        userdata_vol = volumes[1]
        assert userdata_vol.host_path is not None
        assert userdata_vol.persistent_volume_claim is None

    def test_hostpath_userdata_includes_thread_id(self, provisioner_module):
        """hostPath user-data path should include thread_id."""
        provisioner_module.USERDATA_PVC_NAME = ""
        volumes = provisioner_module._build_volumes("my-thread-42")
        userdata_vol = volumes[1]
        path = userdata_vol.host_path.path
        assert "my-thread-42" in path
        assert path.endswith("user-data")
        assert userdata_vol.host_path.type == "DirectoryOrCreate"

    def test_hostpath_userdata_is_scoped_by_user_and_thread(self, provisioner_module):
        provisioner_module.USERDATA_PVC_NAME = ""
        provisioner_module.THREADS_HOST_PATH = "/data/deer-flow"
        assert "user_id" in inspect.signature(provisioner_module._build_volumes).parameters

        volumes = provisioner_module._build_volumes("thread-42", user_id="user-7")

        assert volumes[1].host_path.path == "/data/deer-flow/users/user-7/threads/thread-42/user-data"

    def test_skills_pvc_overrides_hostpath(self, provisioner_module):
        """When SKILLS_PVC_NAME is set, skills volume should use PVC."""
        provisioner_module.SKILLS_PVC_NAME = "my-skills-pvc"
        volumes = provisioner_module._build_volumes("thread-1")
        skills_vol = volumes[0]
        assert skills_vol.persistent_volume_claim is not None
        assert skills_vol.persistent_volume_claim.claim_name == "my-skills-pvc"
        assert skills_vol.persistent_volume_claim.read_only is True
        assert skills_vol.host_path is None

    def test_userdata_pvc_overrides_hostpath(self, provisioner_module):
        """When USERDATA_PVC_NAME is set, user-data volume should use PVC."""
        provisioner_module.USERDATA_PVC_NAME = "my-userdata-pvc"
        volumes = provisioner_module._build_volumes("thread-1")
        userdata_vol = volumes[1]
        assert userdata_vol.persistent_volume_claim is not None
        assert userdata_vol.persistent_volume_claim.claim_name == "my-userdata-pvc"
        assert userdata_vol.host_path is None

    def test_both_pvc_set(self, provisioner_module):
        """When both PVC names are set, both volumes use PVC."""
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        volumes = provisioner_module._build_volumes("thread-1")
        assert volumes[0].persistent_volume_claim is not None
        assert volumes[1].persistent_volume_claim is not None

    def test_returns_three_volumes(self, provisioner_module):
        """Skills, user-data, and ACP workspace must all be mounted."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        assert len(provisioner_module._build_volumes("t")) == 3

        provisioner_module.SKILLS_PVC_NAME = "a"
        provisioner_module.USERDATA_PVC_NAME = "b"
        assert len(provisioner_module._build_volumes("t")) == 3

    def test_volume_names_are_stable(self, provisioner_module):
        """Volume names must stay stable for pod reconciliation."""
        volumes = provisioner_module._build_volumes("thread-1")
        assert volumes[0].name == "skills"
        assert volumes[1].name == "user-data"
        assert volumes[2].name == "acp-workspace"

    def test_hostpath_acp_workspace_is_user_and_thread_scoped(self, provisioner_module):
        provisioner_module.USERDATA_PVC_NAME = ""
        provisioner_module.THREADS_HOST_PATH = "/data/deer-flow"

        volumes = provisioner_module._build_volumes("thread-42", user_id="user-7")

        assert volumes[2].host_path.path == "/data/deer-flow/users/user-7/threads/thread-42/acp-workspace"
        assert volumes[2].host_path.type == "DirectoryOrCreate"


# ── _build_volume_mounts ───────────────────────────────────────────────


class TestBuildVolumeMounts:
    """Tests for _build_volume_mounts: mount paths and subPath behavior."""

    def test_default_no_subpath(self, provisioner_module):
        """hostPath mode should not set sub_path on user-data mount."""
        provisioner_module.USERDATA_PVC_NAME = ""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        userdata_mount = mounts[1]
        assert userdata_mount.sub_path is None

    def test_pvc_sets_user_scoped_subpath(self, provisioner_module):
        """PVC mode should include user_id in the user-data subPath."""
        provisioner_module.USERDATA_PVC_NAME = "my-pvc"
        mounts = provisioner_module._build_volume_mounts("thread-42", user_id="user-7")
        userdata_mount = mounts[1]
        assert userdata_mount.sub_path == "deer-flow/users/user-7/threads/thread-42/user-data"

    def test_pvc_defaults_to_default_user_subpath(self, provisioner_module):
        """Older callers should still land under a stable default user namespace."""
        provisioner_module.USERDATA_PVC_NAME = "my-pvc"
        mounts = provisioner_module._build_volume_mounts("thread-42")
        userdata_mount = mounts[1]
        assert userdata_mount.sub_path == "deer-flow/users/default/threads/thread-42/user-data"

    def test_skills_mount_read_only(self, provisioner_module):
        """Skills mount should always be read-only."""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        assert mounts[0].read_only is True

    def test_userdata_mount_read_write(self, provisioner_module):
        """User-data mount should always be read-write."""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        assert mounts[1].read_only is False

    def test_mount_paths_are_stable(self, provisioner_module):
        """Mount paths must stay /mnt/skills and /mnt/user-data."""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        assert mounts[0].mount_path == "/mnt/skills"
        assert mounts[1].mount_path == "/mnt/user-data"

    def test_mount_names_match_volumes(self, provisioner_module):
        """Mount names should match the volume names."""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        assert mounts[0].name == "skills"
        assert mounts[1].name == "user-data"

    def test_returns_three_mounts(self, provisioner_module):
        assert len(provisioner_module._build_volume_mounts("t")) == 3

    def test_acp_workspace_mount_is_read_only_and_user_scoped_for_pvc(self, provisioner_module):
        provisioner_module.USERDATA_PVC_NAME = "my-pvc"

        mounts = provisioner_module._build_volume_mounts("thread-42", user_id="user-7")

        assert mounts[2].name == "acp-workspace"
        assert mounts[2].mount_path == "/mnt/acp-workspace"
        assert mounts[2].read_only is True
        assert mounts[2].sub_path == "deer-flow/users/user-7/threads/thread-42/acp-workspace"


# ── _build_pod integration ─────────────────────────────────────────────


class TestBuildPodVolumes:
    """Integration: _build_pod should wire volumes and mounts correctly."""

    def test_pod_spec_has_volumes(self, provisioner_module):
        """Pod spec should contain all three standard volumes."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        pod = provisioner_module._build_pod("sandbox-1", "thread-1", sandbox_api_key="test-sandbox-key")
        assert len(pod.spec.volumes) == 3

    def test_pod_spec_has_volume_mounts(self, provisioner_module):
        """Container should have all three standard volume mounts."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        pod = provisioner_module._build_pod("sandbox-1", "thread-1", sandbox_api_key="test-sandbox-key")
        assert len(pod.spec.containers[0].volume_mounts) == 3

    def test_pod_hostpath_mode_uses_user_scoped_volume(self, provisioner_module):
        provisioner_module.USERDATA_PVC_NAME = ""
        provisioner_module.THREADS_HOST_PATH = "/data/deer-flow"

        pod = provisioner_module._build_pod(
            "sandbox-1",
            "thread-1",
            user_id="user-7",
            sandbox_api_key="test-sandbox-key",
        )

        assert pod.spec.volumes[1].host_path.path == "/data/deer-flow/users/user-7/threads/thread-1/user-data"

    def test_pod_pvc_mode_uses_user_scoped_subpath(self, provisioner_module):
        """Pod should use a user-scoped subPath for PVC user-data."""
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        pod = provisioner_module._build_pod(
            "sandbox-1",
            "thread-1",
            user_id="user-7",
            sandbox_api_key="test-sandbox-key",
        )
        assert pod.spec.volumes[0].persistent_volume_claim is not None
        assert pod.spec.volumes[1].persistent_volume_claim is not None
        userdata_mount = pod.spec.containers[0].volume_mounts[1]
        assert userdata_mount.sub_path == "deer-flow/users/user-7/threads/thread-1/user-data"

    def test_pod_pvc_mode_initializes_fresh_subpaths(self, provisioner_module):
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"

        pod = provisioner_module._build_pod(
            "sandbox-1",
            "thread-1",
            user_id="user-7",
            sandbox_api_key="test-sandbox-key",
        )

        assert len(pod.spec.init_containers or []) == 1
        init = pod.spec.init_containers[0]
        assert init.image == provisioner_module.PVC_INIT_IMAGE
        assert init.command == ["sh", "-c"]
        assert init.volume_mounts[0].name == "user-data"
        assert init.volume_mounts[0].mount_path == "/deer-flow-data"
        assert init.volume_mounts[0].sub_path is None
        assert "/deer-flow-data/deer-flow/users/user-7/threads/thread-1/user-data/workspace" in init.args
        assert "/deer-flow-data/deer-flow/users/user-7/threads/thread-1/user-data/uploads" in init.args
        assert "/deer-flow-data/deer-flow/users/user-7/threads/thread-1/user-data/outputs" in init.args
        assert "/deer-flow-data/deer-flow/users/user-7/threads/thread-1/acp-workspace" in init.args

    def test_pod_hostpath_mode_needs_no_init_container(self, provisioner_module):
        provisioner_module.USERDATA_PVC_NAME = ""

        pod = provisioner_module._build_pod(
            "sandbox-1",
            "thread-1",
            user_id="user-7",
            sandbox_api_key="test-sandbox-key",
        )

        assert not pod.spec.init_containers
