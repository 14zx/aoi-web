"""Установка весов в ``models/`` для CI / portable-сборки.

Порядок:
1. ``MODELS_BUNDLE_URL`` — ZIP с GitHub Release (полный комплект, включая ``aoi_unified.pt``).
2. Иначе — скачивание публичных весов (Hugging Face + пресет PKU).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"


def _auth_headers(accept: str = "*/*") -> dict[str, str]:
    headers = {"User-Agent": "AOI-Web-CI", "Accept": accept}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _download(url: str, dest: Path, *, headers: dict[str, str] | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"GET {url}")
    req = urllib.request.Request(url, headers=headers or _auth_headers())
    with urllib.request.urlopen(req, timeout=600) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out)
    print(f"  -> {dest} ({dest.stat().st_size / (1024 * 1024):.1f} MiB)")


def _extract_models_zip(zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        # Архив от package_models_release: корень "models/..."
        if any(n.startswith("models/") for n in names):
            zf.extractall(ROOT)
        else:
            MODELS.mkdir(parents=True, exist_ok=True)
            zf.extractall(MODELS)


def _try_bundle_url(url: str) -> bool:
    url = url.strip()
    if not url:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "models-bundle.zip"
        try:
            _download(url, zpath)
            _extract_models_zip(zpath)
            return True
        except OSError as exc:
            print(f"WARN: не удалось загрузить архив весов: {exc}", file=sys.stderr)
            return False


def _try_github_release_bundle() -> bool:
    """Download models ZIP from a GitHub Release (works for private repos with GITHUB_TOKEN)."""
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    tag = os.environ.get("MODELS_RELEASE_TAG", "v1.0.0-models").strip()
    asset_name = os.environ.get("MODELS_ASSET_NAME", "AOI-Web-models-1.0.0.zip").strip()
    if not repo or not tag:
        return False

    api_url = f"https://api.github.com/repos/{repo}/releases/tags/{urllib.parse.quote(tag)}"
    print(f"GitHub API release: {tag}")
    req = urllib.request.Request(
        api_url,
        headers=_auth_headers("application/vnd.github+json"),
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            release = json.load(resp)
    except OSError as exc:
        print(f"WARN: release lookup failed: {exc}", file=sys.stderr)
        return False

    assets = release.get("assets") or []
    asset = next((a for a in assets if a.get("name") == asset_name), None)
    if not asset:
        print(f"WARN: asset {asset_name!r} not on release {tag}", file=sys.stderr)
        return False

    asset_api = asset["url"]
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / asset_name
        try:
            _download(
                asset_api,
                zpath,
                headers=_auth_headers("application/octet-stream"),
            )
            _extract_models_zip(zpath)
            return True
        except OSError as exc:
            print(f"WARN: release asset download failed: {exc}", file=sys.stderr)
            return False


def _download_public_presets() -> None:
    subprocess.run([sys.executable, "-m", "pip", "install", "huggingface_hub"], check=True)
    subprocess.run([sys.executable, "-m", "scripts.download_pretrained"], cwd=ROOT, check=True)
    subprocess.run(
        [sys.executable, "-m", "scripts.download_datasets", "--only", "pku"],
        cwd=ROOT,
        check=True,
    )


def _list_pt() -> list[Path]:
    if not MODELS.is_dir():
        return []
    return sorted(MODELS.rglob("*.pt"))


def main() -> int:
    MODELS.mkdir(parents=True, exist_ok=True)

    url = os.environ.get("MODELS_BUNDLE_URL", "").strip()
    got_bundle = _try_bundle_url(url) if url else False
    if not got_bundle:
        got_bundle = _try_github_release_bundle()
    if not got_bundle and not _list_pt():
        print("WARN: models bundle missing; downloading public presets (no aoi_unified.pt)...")
        _download_public_presets()

    pts = _list_pt()
    print(f"\nФайлов .pt в models/: {len(pts)}")
    for p in pts[:20]:
        print(f"  {p.relative_to(ROOT)} ({p.stat().st_size / (1024 * 1024):.1f} MiB)")
    if len(pts) > 20:
        print(f"  ... и ещё {len(pts) - 20}")

    unified = MODELS / "aoi_unified.pt"
    if not unified.is_file():
        print(
            "WARN: нет models/aoi_unified.pt — загрузите Release с полным архивом "
            "(см. models/README.md, scripts/package_models_release.ps1).",
            file=sys.stderr,
        )
    else:
        print(f"OK: {unified.name}")

    if not pts:
        print("ERROR: в models/ нет ни одного .pt", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
