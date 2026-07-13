"""Factual evidence-reference normalization for Command Room records.

The runtime preserves provenance supplied by adapters or AIs. It does not read
natural-language references to decide evidence strength, trust, or quality.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.I | re.S)
_OPEN_THINK_RE = re.compile(r"<think\b[^>]*>.*$", re.I | re.S)
_SECRET_LIKE_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{12,}|ak(?:ia|as)[a-z0-9]{12,}|"
    r"(?:api[_-]?key|token|secret|password|authorization)\s*[:=]\s*['\"]?[^'\"\s]+)"
)
_PUBLIC_TEXT_MAX_CHARS = 500

SourceKind = Literal[
    "command_output",
    "artifact",
    "hash",
    "diff",
    "log",
    "path",
    "source_ref",
    "screenshot",
    "self_claim",
    "output_ref",
    "unknown",
]
_SOURCE_KINDS = set(SourceKind.__args__)


@dataclass(frozen=True)
class EvidenceRef:
    """Structured, redacted provenance for judgment by an AI."""

    ref_id: str
    thread_id: str
    run_id: str | None
    round_id: str | None
    task_id: str | None
    source_kind: SourceKind
    strength: None
    claim: str
    ref: str
    excerpt: str | None
    sha256: str | None
    produced_by: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_evidence_ref(
    ref: str | dict[str, Any] | None,
    *,
    thread_id: str,
    run_id: str | None = None,
    round_id: str | None = None,
    task_id: str | None = None,
    claim: str = "",
    produced_by: str = "runtime",
    created_at: str | None = None,
    source_kind: str | None = None,
    excerpt: str | None = None,
    sha256: str | None = None,
    ref_id: str | None = None,
) -> dict[str, Any]:
    """Normalize explicitly supplied fields without interpreting prose."""

    raw_ref = _raw_ref_text(ref)
    raw_claim = claim or _mapping_text(ref, "claim")
    raw_excerpt = excerpt if excerpt is not None else _mapping_text(ref, "excerpt") or raw_ref
    cleaned_ref = _public_text(raw_ref)
    cleaned_excerpt = _public_text(raw_excerpt) if raw_excerpt else None
    cleaned_source_kind = _normalize_source_kind(source_kind or _mapping_text(ref, "source_kind"))
    cleaned_sha256 = _clean_sha256(sha256 or _mapping_text(ref, "sha256"))
    created = created_at or _mapping_text(ref, "created_at") or datetime.now(UTC).isoformat()
    evidence = EvidenceRef(
        ref_id=ref_id or _stable_ref_id(thread_id, run_id, round_id, task_id, cleaned_ref, cleaned_source_kind),
        thread_id=thread_id,
        run_id=run_id if run_id is not None else _optional_mapping_text(ref, "run_id"),
        round_id=round_id if round_id is not None else _optional_mapping_text(ref, "round_id"),
        task_id=task_id if task_id is not None else _optional_mapping_text(ref, "task_id"),
        source_kind=cleaned_source_kind,
        strength=None,
        claim=_public_text(raw_claim),
        ref=cleaned_ref,
        excerpt=cleaned_excerpt,
        sha256=cleaned_sha256,
        produced_by=_public_text(produced_by or _mapping_text(ref, "produced_by") or "runtime"),
        created_at=str(created),
    )
    return evidence.as_dict()


def _raw_ref_text(ref: str | dict[str, Any] | None) -> str:
    if isinstance(ref, str):
        return ref
    if isinstance(ref, dict):
        for key in ("ref", "virtual_path", "path", "source_ref", "output_ref", "url"):
            value = ref.get(key)
            if value is not None:
                return str(value)
    return ""


def _mapping_text(ref: Any, key: str) -> str:
    value = ref.get(key) if isinstance(ref, dict) else None
    return "" if value is None else str(value)


def _optional_mapping_text(ref: Any, key: str) -> str | None:
    text = _mapping_text(ref, key).strip()
    return text or None


def _normalize_source_kind(value: str | None) -> SourceKind:
    kind = (value or "unknown").strip()
    return kind if kind in _SOURCE_KINDS else "unknown"  # type: ignore[return-value]


def _public_text(value: Any, *, max_chars: int = _PUBLIC_TEXT_MAX_CHARS) -> str:
    text = "" if value is None else str(value)
    text = _THINK_BLOCK_RE.sub("", text)
    text = _OPEN_THINK_RE.sub("", text)
    text = _SECRET_LIKE_RE.sub("[redacted]", text)
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    marker = "... [truncated]"
    return f"{text[: max(0, max_chars - len(marker))]}{marker}"


def redact_evidence_text(value: Any, *, max_chars: int = _PUBLIC_TEXT_MAX_CHARS) -> str:
    """Return bounded public text for runtime-observed metadata."""

    return _public_text(value, max_chars=max_chars)


def _clean_sha256(value: str | None) -> str | None:
    cleaned = (value or "").strip().lower()
    return cleaned if re.fullmatch(r"[a-f0-9]{6,64}", cleaned) else None


def _stable_ref_id(
    thread_id: str,
    run_id: str | None,
    round_id: str | None,
    task_id: str | None,
    ref: str,
    source_kind: str,
) -> str:
    payload = "\0".join([thread_id, run_id or "", round_id or "", task_id or "", source_kind, ref])
    return f"evref_{sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def summarize_evidence_refs(refs: Iterable[str | None]) -> dict[str, object]:
    """Return counts only; an AI decides whether the references are adequate."""

    values = [str(ref).strip() for ref in refs if ref is not None and str(ref).strip()]
    return {
        "total": len(values),
        "refs": values,
        "quality_verdict": None,
        "auto_rework": False,
    }


__all__ = ["EvidenceRef", "normalize_evidence_ref", "redact_evidence_text", "summarize_evidence_refs"]
