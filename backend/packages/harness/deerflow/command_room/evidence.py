"""Mechanical evidence-reference signal helpers for Command Room.

These helpers expose hard signal/boundary hints only. They do not decide
project quality, PASS/FAIL, or trigger any rework loop.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal

_STRONG_TOKENS = (
    "exit code",
    "exit_code",
    "returncode",
    "stdout",
    "stderr",
    "log:",
    "logs/",
    ".log",
    "artifact:",
    "artifacts/",
    "hash:",
    "sha256",
    "sha1",
    "md5",
    "diff --git",
    "git diff",
    "source_ref:",
    "screenshot:",
)
_PATH_RE = re.compile(r"(?:^|\s)(?:/|\./|\.\./|[\w.-]+/)[\w./-]+(?:\.py|\.md|\.json|\.jsonl|\.txt|\.log|\.yaml|\.yml|\.diff|\.patch|\.png|\.jpg|\.jpeg|\.webp)(?::\d+)?")
_COMMAND_RE = re.compile(r"(?:^|\b)(?:command|cmd|\$|pytest|python -m pytest|make|ruff|mypy|npm|pnpm|yarn)\b", re.I)
_OUTPUT_RE = re.compile(r"(?:^|\b)(?:output|stdout|stderr|exit code|exit_code|returncode)\b", re.I)
_TESTS_PASSED_ALONE_RE = re.compile(r"^\s*(?:tests?\s+passed|测试通过|passed)\s*[.!。]?\s*$", re.I)
_OUTPUT_REF_ONLY_RE = re.compile(r"^\s*output[_ -]?ref\s*[:=]?\s*\S+\s*$", re.I)
_SUMMARY_ONLY_RE = re.compile(r"^\s*(?:summary|总结)\s*[:：].*$", re.I | re.S)
_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.I | re.S)
_OPEN_THINK_RE = re.compile(r"<think\b[^>]*>.*$", re.I | re.S)
_SECRET_LIKE_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{12,}|ak(?:ia|as)[a-z0-9]{12,}|"
    r"(?:api[_-]?key|token|secret|password|authorization)\s*[:=]\s*['\"]?[^'\"\s]+)"
)
_SHA256_RE = re.compile(r"(?i)\bsha256\s*[:=]\s*([a-f0-9]{6,64})\b")
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
EvidenceStrength = Literal["Strong", "Weak", "Unverified"]
_SOURCE_KINDS = set(SourceKind.__args__)
_STRONG_SOURCE_KINDS = {"command_output", "artifact", "hash", "diff", "log", "path", "source_ref", "screenshot"}
_WEAK_SOURCE_KINDS = {"self_claim", "output_ref"}


@dataclass(frozen=True)
class EvidenceSignal:
    """Mechanical signal extracted from one evidence reference.

    ``trusted_source`` is intentionally narrow: only observable runtime/adapter
    provenance (commands, tool output, logs, files, artifacts, hashes, diffs,
    etc.) can make a strong evidence signal. Bare model/worker text such as
    ``tests passed`` or self-claimed ``verified=true`` stays summary-level.
    """

    ref: str
    strong: bool
    weak_reasons: tuple[str, ...]
    strong_reasons: tuple[str, ...]
    trusted_source: bool = False
    source_kind: str = "unknown"


@dataclass(frozen=True)
class EvidenceRef:
    """Structured, redacted evidence reference for AI evidence judgment."""

    ref_id: str
    thread_id: str
    run_id: str | None
    round_id: str | None
    task_id: str | None
    source_kind: SourceKind
    strength: EvidenceStrength
    claim: str
    ref: str
    excerpt: str | None
    sha256: str | None
    produced_by: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_evidence_ref(ref: str | None) -> EvidenceSignal:
    """Classify obvious strong/weak evidence signals mechanically.

    Strong means the ref contains reproducible hard signals such as command plus
    output/exit code, log/artifact/hash/diff/path. It is not a quality verdict.
    """

    text = (ref or "").strip()
    lower = text.lower()
    weak: list[str] = []
    strong: list[str] = []

    if not text:
        weak.append("empty")
    if _TESTS_PASSED_ALONE_RE.match(text):
        weak.append("tests-passed-alone")
    if _OUTPUT_REF_ONLY_RE.match(text) or ("output_ref" in lower and not any(t in lower for t in _STRONG_TOKENS)):
        weak.append("output-ref-only")
    if _SUMMARY_ONLY_RE.match(text) and not any(t in lower for t in _STRONG_TOKENS):
        weak.append("summary-only")

    has_command = bool(_COMMAND_RE.search(text))
    has_output = bool(_OUTPUT_RE.search(text))
    if has_command and has_output:
        strong.append("command-output-or-exit-code")
    for token in _STRONG_TOKENS:
        if token in lower:
            strong.append(token.rstrip(":"))
    if _PATH_RE.search(text):
        strong.append("path")

    strong_unique = tuple(dict.fromkeys(strong))
    weak_unique = tuple(dict.fromkeys(weak))
    trusted_source = bool(strong_unique) and "tests-passed-alone" not in weak_unique
    return EvidenceSignal(
        ref=text,
        strong=trusted_source,
        weak_reasons=weak_unique,
        strong_reasons=strong_unique,
        trusted_source=trusted_source,
        source_kind=_source_kind(text, lower, weak_unique, has_command=has_command, has_output=has_output),
    )


def _source_kind(text: str, lower: str, weak_reasons: tuple[str, ...], *, has_command: bool, has_output: bool) -> str:
    if not text:
        return "empty"
    if "output-ref-only" in weak_reasons:
        return "output_ref"
    if "tests-passed-alone" in weak_reasons or "summary-only" in weak_reasons:
        return "self_claim"
    if has_command and has_output:
        return "command_output"
    if "artifact:" in lower or "artifacts/" in lower:
        return "artifact"
    if "hash:" in lower or "sha256" in lower or "sha1" in lower or "md5" in lower:
        return "hash"
    if "diff --git" in lower or "git diff" in lower:
        return "diff"
    if "log:" in lower or "logs/" in lower or ".log" in lower:
        return "log"
    if "source_ref:" in lower:
        return "source_ref"
    if "screenshot:" in lower:
        return "screenshot"
    if _PATH_RE.search(text):
        return "path"
    return "unknown"


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
    """Return a structured EvidenceRef dict from old strings or dict-like refs.

    This is a mechanical normalization step only. It labels provenance and
    obvious strength signals; it does not decide quality or trigger review.
    """

    raw_ref = _raw_ref_text(ref)
    raw_claim = claim or _mapping_text(ref, "claim")
    raw_excerpt = excerpt if excerpt is not None else _mapping_text(ref, "excerpt") or raw_ref
    cleaned_ref = _public_text(raw_ref)
    cleaned_excerpt = _public_text(raw_excerpt) if raw_excerpt else None
    signal = analyze_evidence_ref(cleaned_ref)
    cleaned_source_kind = _normalize_source_kind(source_kind or _mapping_text(ref, "source_kind") or signal.source_kind)
    cleaned_sha256 = _clean_sha256(sha256 or _mapping_text(ref, "sha256") or _extract_sha256(raw_ref))
    created = created_at or _mapping_text(ref, "created_at") or datetime.now(UTC).isoformat()
    evidence = EvidenceRef(
        ref_id=ref_id or _stable_ref_id(thread_id, run_id, round_id, task_id, cleaned_ref, cleaned_source_kind),
        thread_id=thread_id,
        run_id=run_id if run_id is not None else _optional_mapping_text(ref, "run_id"),
        round_id=round_id if round_id is not None else _optional_mapping_text(ref, "round_id"),
        task_id=task_id if task_id is not None else _optional_mapping_text(ref, "task_id"),
        source_kind=cleaned_source_kind,
        strength=_mechanical_strength(signal, cleaned_source_kind),
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
    if value is None:
        return ""
    return str(value)


def _optional_mapping_text(ref: Any, key: str) -> str | None:
    text = _mapping_text(ref, key).strip()
    return text or None


def _normalize_source_kind(value: str | None) -> SourceKind:
    kind = (value or "unknown").strip()
    return kind if kind in _SOURCE_KINDS else "unknown"  # type: ignore[return-value]


def _mechanical_strength(signal: EvidenceSignal, source_kind: SourceKind) -> EvidenceStrength:
    if source_kind in _WEAK_SOURCE_KINDS:
        return "Weak"
    if source_kind in _STRONG_SOURCE_KINDS or signal.strong:
        return "Strong"
    if signal.weak_reasons:
        return "Weak"
    return "Unverified"


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
    """Return bounded public text for runtime-observed evidence metadata."""

    return _public_text(value, max_chars=max_chars)


def _extract_sha256(text: str) -> str | None:
    match = _SHA256_RE.search(text or "")
    return match.group(1) if match else None


def _clean_sha256(value: str | None) -> str | None:
    cleaned = (value or "").strip().lower()
    if re.fullmatch(r"[a-f0-9]{6,64}", cleaned):
        return cleaned
    return None


def _stable_ref_id(thread_id: str, run_id: str | None, round_id: str | None, task_id: str | None, ref: str, source_kind: str) -> str:
    payload = "\0".join([thread_id, run_id or "", round_id or "", task_id or "", source_kind, ref])
    return f"evref_{sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def summarize_evidence_refs(refs: Iterable[str | None]) -> dict[str, object]:
    """Return aggregate hard-signal hints without making a PASS/FAIL judgment."""

    signals = [analyze_evidence_ref(ref) for ref in refs]
    return {
        "has_strong_signal": any(signal.strong for signal in signals),
        "strong_count": sum(1 for signal in signals if signal.strong),
        "weak_count": sum(1 for signal in signals if signal.weak_reasons),
        "signals": signals,
        "quality_verdict": None,
        "auto_rework": False,
    }


__all__ = ["EvidenceRef", "EvidenceSignal", "analyze_evidence_ref", "normalize_evidence_ref", "redact_evidence_text", "summarize_evidence_refs"]
