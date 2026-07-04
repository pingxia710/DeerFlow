"""Mechanical evidence-reference signal helpers for Command Room.

These helpers expose hard signal/boundary hints only. They do not decide
project quality, PASS/FAIL, or trigger any rework loop.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

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
)
_PATH_RE = re.compile(r"(?:^|\s)(?:/|\./|\.\./|[\w.-]+/)[\w./-]+(?:\.py|\.md|\.json|\.jsonl|\.txt|\.log|\.yaml|\.yml|\.diff|\.patch)(?::\d+)?")
_COMMAND_RE = re.compile(r"(?:^|\b)(?:command|cmd|\$|pytest|python -m pytest|make|ruff|mypy|npm|pnpm|yarn)\b", re.I)
_OUTPUT_RE = re.compile(r"(?:^|\b)(?:output|stdout|stderr|exit code|exit_code|returncode)\b", re.I)
_TESTS_PASSED_ALONE_RE = re.compile(r"^\s*(?:tests?\s+passed|测试通过|passed)\s*[.!。]?\s*$", re.I)
_OUTPUT_REF_ONLY_RE = re.compile(r"^\s*output[_ -]?ref\s*[:=]?\s*\S+\s*$", re.I)
_SUMMARY_ONLY_RE = re.compile(r"^\s*(?:summary|总结)\s*[:：].*$", re.I | re.S)


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
    if _PATH_RE.search(text):
        return "path"
    return "unknown"


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


__all__ = ["EvidenceSignal", "analyze_evidence_ref", "summarize_evidence_refs"]
