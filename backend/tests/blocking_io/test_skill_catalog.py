"""Regression anchor: skill catalog routes must not block the event loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.gateway.routers.skills import list_skill_catalog
from deerflow.config.extensions_config import ExtensionsConfig, SkillCatalogSourceConfig
from deerflow.skills.catalog import install_catalog_skill

pytestmark = pytest.mark.asyncio


def _build_catalog(tmp_path: Path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    archive = tmp_path / "catalog-skill.skill"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "catalog-skill/SKILL.md",
            "---\nname: catalog-skill\ndescription: Blocking IO regression fixture.\n---\n",
        )
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "catalog-skill",
                        "archiveUrl": archive.name,
                        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                        "riskLevel": "low",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return SimpleNamespace(
        skills=SimpleNamespace(
            get_skills_path=lambda: skills_root,
            container_path="/mnt/skills",
            use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage",
        ),
        extensions=ExtensionsConfig(
            skillCatalogSources={
                "official": SkillCatalogSourceConfig(
                    url=str(index),
                    trustLevel="official",
                )
            }
        ),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )


# Regression: ISSUE-BE-001 — catalog file/network IO blocked Gateway streams.
# Found by /qa on 2026-07-10
# Report: .gstack/qa-reports/qa-report-localhost-2026-07-10.md
async def test_catalog_list_and_install_do_not_block_event_loop(tmp_path: Path, monkeypatch) -> None:
    config = await asyncio.to_thread(_build_catalog, tmp_path)

    async def _allow_scan(*args, **kwargs):
        return SimpleNamespace(decision="allow", reason="fixture")

    monkeypatch.setattr("deerflow.skills.catalog.scan_skill_content", _allow_scan)
    monkeypatch.setattr("deerflow.skills.installer.scan_skill_content", _allow_scan)

    request = SimpleNamespace(_deerflow_test_bypass_auth=True)
    response = await list_skill_catalog(request=request, config=config)
    assert [item["name"] for item in response["skills"]] == ["catalog-skill"]

    result = await install_catalog_skill(config, "official", "catalog-skill")
    assert result["success"] is True
    assert await asyncio.to_thread((config.skills.get_skills_path() / "custom" / "catalog-skill" / "SKILL.md").is_file)
