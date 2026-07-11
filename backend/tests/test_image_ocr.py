from __future__ import annotations

import stat
import subprocess
from pathlib import Path

from deerflow.utils import image_ocr


def test_macos_vision_uses_private_unique_script_and_cleans_up(tmp_path, monkeypatch):
    protected_path = tmp_path / "protected.txt"
    protected_path.write_text("do not overwrite", encoding="utf-8")
    fixed_script_path = tmp_path / "deerflow-image-ocr.swift"
    fixed_script_path.symlink_to(protected_path)
    image_path = tmp_path / "image.png"
    image_path.touch()
    observed: dict[str, object] = {}

    def fake_run(args: list[str], **_kwargs: object):
        script_path = Path(args[1])
        observed.update(
            path=script_path,
            content=script_path.read_text(encoding="utf-8"),
            mode=stat.S_IMODE(script_path.stat().st_mode),
            is_symlink=script_path.is_symlink(),
        )
        return subprocess.CompletedProcess(args, 0, "recognized\n", "")

    monkeypatch.setattr(image_ocr.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(image_ocr.shutil, "which", lambda _command: "/usr/bin/swift")
    monkeypatch.setattr(image_ocr.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(image_ocr.subprocess, "run", fake_run)

    assert image_ocr._run_macos_vision(image_path, 1) == "recognized"
    assert protected_path.read_text(encoding="utf-8") == "do not overwrite"
    assert observed["path"] != fixed_script_path
    assert observed["content"] == image_ocr._SWIFT_OCR_SOURCE
    assert observed["mode"] == 0o600
    assert observed["is_symlink"] is False
    assert not Path(observed["path"]).exists()
