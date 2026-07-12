"""Small OCR helpers for uploaded images."""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

OCR_SIDECAR_SUFFIX = ".ocr.txt"
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_OCR_CHARS = 12000

_SWIFT_OCR_SOURCE = r"""
import Foundation
import Vision
import ImageIO

let path = CommandLine.arguments[1]
let url = URL(fileURLWithPath: path)
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
  exit(2)
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
let handler = VNImageRequestHandler(cgImage: image, options: [:])
try handler.perform([request])
let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
print(lines.joined(separator: "\n"))
"""


def is_supported_image_path(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS


def ocr_sidecar_path(path: Path) -> Path:
    return path.with_name(f"{path.name}{OCR_SIDECAR_SUFFIX}")


def is_ocr_sidecar(path: Path) -> bool:
    return path.name.lower().endswith(OCR_SIDECAR_SUFFIX)


def _clean_text(text: str) -> str:
    cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return cleaned[:MAX_OCR_CHARS].strip()


def _run_tesseract(path: Path, timeout_seconds: int) -> str | None:
    if not shutil.which("tesseract"):
        return None
    result = subprocess.run(
        ["tesseract", str(path), "stdout", "-l", "chi_sim+chi_tra+eng"],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    return _clean_text(result.stdout) if result.returncode == 0 else None


def _run_macos_vision(path: Path, timeout_seconds: int) -> str | None:
    if platform.system() != "Darwin" or not shutil.which("swift"):
        return None
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".swift") as script_file:
        script_file.write(_SWIFT_OCR_SOURCE)
        script_file.flush()
        result = subprocess.run(
            ["swift", script_file.name, str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    return _clean_text(result.stdout) if result.returncode == 0 else None


def extract_image_text(path: Path, *, timeout_seconds: int = 45) -> str | None:
    if not is_supported_image_path(path):
        return None
    try:
        return _run_tesseract(path, timeout_seconds) or _run_macos_vision(path, timeout_seconds)
    except Exception:
        logger.debug("Image OCR failed for %s", path, exc_info=True)
        return None
