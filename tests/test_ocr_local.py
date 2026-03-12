from pathlib import Path

from src import ocr_local


def test_extract_ocr_text_returns_empty_for_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.png"
    assert ocr_local.extract_ocr_text(missing) == ""


def test_extract_ocr_text_normalizes_and_truncates(monkeypatch, tmp_path: Path) -> None:
    image = tmp_path / "img.png"
    image.write_bytes(b"fake")

    monkeypatch.setattr(ocr_local.shutil, "which", lambda _name: "/usr/bin/tesseract")

    class _Proc:
        returncode = 0
        stdout = "  hello\t\tworld \n\n\nnext line  "

    def _fake_run(*_args, **_kwargs):
        return _Proc()

    monkeypatch.setattr(ocr_local.subprocess, "run", _fake_run)

    out = ocr_local.extract_ocr_text(image, max_chars=12)
    assert out == "hello world"
