from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
from typing import Any

PYTHON_PACKAGES = [
    "pdfbank", "PyMuPDF", "pypdf", "pdfplumber", "camelot-py", "opencv-python-headless",
    "pytesseract", "ocrmypdf", "onnxruntime", "Pillow", "numpy", "pandas", "pydantic", "typer", "Jinja2",
]
COMMANDS = {
    "tesseract": ["tesseract", "--version"],
    "poppler_pdftotext": ["pdftotext", "-v"],
    "poppler_pdfinfo": ["pdfinfo", "-v"],
    "qpdf": ["qpdf", "--version"],
    "mutool": ["mutool", "-v"],
    "ghostscript": ["gs", "--version"],
    "ocrmypdf_cli": ["ocrmypdf", "--version"],
    "libreoffice": ["libreoffice", "--version"],
    "emacs": ["emacs", "--version"],
    "firefox": ["firefox-esr", "--version"],
}


def _command_version(argv: list[str]) -> str | None:
    try:
        cp = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10, check=False)
        first = cp.stdout.strip().splitlines()
        return first[0] if first else None
    except Exception:
        return None


def version_report(model_dir: str | Path = "/models/pdfbank") -> dict[str, Any]:
    os_release = {}
    p = Path("/etc/os-release")
    if p.exists():
        for line in p.read_text(errors="replace").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                os_release[k] = v.strip('"')
    packages = {}
    for name in PYTHON_PACKAGES:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    commands = {name: _command_version(argv) for name, argv in COMMANDS.items()}
    models = {}
    manifest = Path(model_dir) / "manifest.json"
    if manifest.exists():
        try:
            models = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception as exc:
            models = {"error": str(exc)}
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "os_release": os_release,
        "python_packages": packages,
        "commands": commands,
        "models": models,
    }
