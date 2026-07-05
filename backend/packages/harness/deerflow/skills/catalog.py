"""Skill catalog listing, preview, and install helpers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from deerflow.config.app_config import AppConfig
from deerflow.config.extensions_config import SkillCatalogSourceConfig
from deerflow.skills.security_scanner import scan_skill_content
from deerflow.skills.storage import get_or_new_skill_storage
from deerflow.skills.storage.skill_storage import SkillStorage
from deerflow.skills.types import SKILL_MD_FILE

_SAFE_SOURCE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PREVIEW_DIR = ".catalog-preview"


class SkillCatalogEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    version: str = ""
    source_url: str = Field(default="", alias="sourceUrl")
    archive_url: str = Field(alias="archiveUrl")
    commit: str | None = None
    sha256: str | None = None
    license: str | None = None
    description: str = ""
    allowed_tools: list[str] = Field(default_factory=list, alias="allowedTools")
    risk_level: Literal["low", "medium", "high"] = Field(default="medium", alias="riskLevel")
    scanner_summary: dict[str, Any] = Field(default_factory=dict, alias="scannerSummary")
    approval_required: bool = Field(default=False, alias="approvalRequired")
    installed: bool = False
    enabled: bool = False

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return SkillStorage.validate_skill_name(value)


def _safe_source_name(name: str) -> str:
    if not _SAFE_SOURCE_RE.fullmatch(name) or ".." in name:
        raise ValueError("Catalog source name must contain only letters, digits, dot, underscore, or hyphen.")
    return name


def _read_url_bytes(url: str) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in {"http", "https"}:
        with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 - URL comes from configured catalog source
            return response.read()
    if parsed.scheme == "file":
        return Path(urllib.request.url2pathname(parsed.path)).read_bytes()
    if parsed.scheme:
        raise ValueError(f"Unsupported catalog URL scheme: {parsed.scheme}")
    return Path(url).read_bytes()


async def _read_url_bytes_async(url: str) -> bytes:
    return await asyncio.to_thread(_read_url_bytes, url)


def _join_url(base_url: str, maybe_relative: str) -> str:
    if urllib.parse.urlparse(maybe_relative).scheme:
        return maybe_relative
    if urllib.parse.urlparse(base_url).scheme in {"http", "https", "file"}:
        return urllib.parse.urljoin(base_url, maybe_relative)
    return str((Path(base_url).parent / maybe_relative).resolve())


def _preview_archive_path(app_config: AppConfig, source_name: str, skill_name: str) -> Path:
    source = _safe_source_name(source_name)
    skill = get_or_new_skill_storage(app_config=app_config).validate_skill_name(skill_name)
    return app_config.skills.get_skills_path() / _PREVIEW_DIR / source / f"{skill}.skill"


def verify_archive_hash(data: bytes, expected_sha256: str | None) -> str:
    actual = hashlib.sha256(data).hexdigest()
    if expected_sha256 and actual.lower() != expected_sha256.lower():
        raise ValueError("Skill archive sha256 mismatch")
    return actual


def _source_config(app_config: AppConfig, source_name: str) -> SkillCatalogSourceConfig:
    source = app_config.extensions.skill_catalog_sources.get(source_name)
    if source is None or not source.enabled:
        raise ValueError(f"Skill catalog source '{source_name}' is not enabled")
    return source


def _catalog_entries_for_source(app_config: AppConfig, source_name: str) -> tuple[SkillCatalogSourceConfig, list[SkillCatalogEntry]]:
    source = _source_config(app_config, source_name)
    raw = json.loads(_read_url_bytes(source.url).decode("utf-8"))
    if isinstance(raw, list):
        raw_entries = raw
    elif isinstance(raw, dict):
        raw_entries = raw.get("skills", [])
    else:
        raise ValueError("Skill catalog index must be a JSON object or list")
    if not isinstance(raw_entries, list):
        raise ValueError("Skill catalog index field 'skills' must be a list")

    installed = {skill.name: skill for skill in get_or_new_skill_storage(app_config=app_config).load_skills(enabled_only=False)}
    entries: list[SkillCatalogEntry] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        entry = SkillCatalogEntry.model_validate(item)
        archive_url = _join_url(source.url, entry.archive_url)
        entries.append(
            entry.model_copy(
                update={
                    "source_url": source.url,
                    "archive_url": archive_url,
                    "installed": entry.name in installed,
                    "enabled": bool(installed.get(entry.name).enabled) if entry.name in installed else False,
                    "approval_required": entry.approval_required or entry.risk_level == "high",
                }
            )
        )
    return source, entries


def list_catalog_entries(app_config: AppConfig) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for source_name, source in app_config.extensions.skill_catalog_sources.items():
        if not source.enabled:
            continue
        _, source_entries = _catalog_entries_for_source(app_config, source_name)
        for entry in source_entries:
            data = entry.model_dump(by_alias=True)
            data["source"] = source_name
            data["trustLevel"] = source.trust_level
            entries.append(data)
    return entries


def _find_entry(app_config: AppConfig, source_name: str, skill_name: str) -> tuple[SkillCatalogSourceConfig, SkillCatalogEntry]:
    source, entries = _catalog_entries_for_source(app_config, source_name)
    normalized = get_or_new_skill_storage(app_config=app_config).validate_skill_name(skill_name)
    for entry in entries:
        if entry.name == normalized:
            return source, entry
    raise ValueError(f"Skill '{skill_name}' not found in catalog source '{source_name}'")


def _archive_skill_md(data: bytes) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "skill.skill"
        archive.write_bytes(data)
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                if Path(name).name == SKILL_MD_FILE:
                    return zf.read(name).decode("utf-8")
    raise ValueError("Skill archive does not contain SKILL.md")


async def fetch_skill_preview(app_config: AppConfig, source_name: str, skill_name: str) -> dict[str, Any]:
    source, entry = _find_entry(app_config, source_name, skill_name)
    if urllib.parse.urlparse(entry.archive_url).scheme in {"http", "https"} and source.trust_level == "community" and not entry.sha256:
        raise ValueError("Community catalog entries must include sha256 before preview or install")
    data = await _read_url_bytes_async(entry.archive_url)
    actual_sha256 = verify_archive_hash(data, entry.sha256)
    preview_path = _preview_archive_path(app_config, source_name, entry.name)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(preview_path.write_bytes, data)
    skill_md = _archive_skill_md(data)
    scan = await scan_skill_content(skill_md, executable=False, location=f"{entry.name}/{SKILL_MD_FILE}", app_config=app_config)
    scanner_summary = {"decision": scan.decision, "reason": scan.reason}
    return {
        **entry.model_dump(by_alias=True),
        "source": source_name,
        "trustLevel": source.trust_level,
        "sha256": actual_sha256,
        "previewPath": str(preview_path),
        "skillMarkdown": skill_md,
        "scannerSummary": scanner_summary,
        "approvalRequired": entry.approval_required or entry.risk_level == "high" or scan.decision == "block",
    }


async def install_catalog_skill(app_config: AppConfig, source_name: str, skill_name: str) -> dict[str, Any]:
    preview = await fetch_skill_preview(app_config, source_name, skill_name)
    if preview["approvalRequired"]:
        return {
            "success": False,
            "approval_required": True,
            "skill_name": preview["name"],
            "message": "Skill catalog install requires human approval before local mutation.",
            "scanner_summary": preview["scannerSummary"],
        }
    result = await get_or_new_skill_storage(app_config=app_config).ainstall_skill_from_archive(preview["previewPath"])
    get_or_new_skill_storage(app_config=app_config).append_history(
        result["skill_name"],
        {
            "action": "catalog_install",
            "author": "catalog",
            "source": source_name,
            "source_url": preview["sourceUrl"],
            "archive_url": preview["archiveUrl"],
            "sha256": preview["sha256"],
            "scanner": preview["scannerSummary"],
        },
    )
    return {**result, "approval_required": False, "scanner_summary": preview["scannerSummary"]}


__all__ = ["SkillCatalogEntry", "fetch_skill_preview", "install_catalog_skill", "list_catalog_entries", "verify_archive_hash"]
