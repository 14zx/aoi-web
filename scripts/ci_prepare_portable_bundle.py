"""Подготовка файлов для PyInstaller на чистом checkout (GitHub Actions).

Создаёт ``.env``, ``aoi.db``, ``storage/``, TLS-сертификаты — всё, что
ожидает ``build/AOI-Web-Portable-HTTPS.spec``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> int:
    root = _root()
    env_example = root / ".env.example"
    env_file = root / ".env"
    if env_example.is_file() and not env_file.is_file():
        shutil.copy(env_example, env_file)
        print(f"Copied {env_example.name} -> .env")

    unified = root / "models" / "aoi_unified.pt"
    if unified.is_file() and env_file.is_file():
        text = env_file.read_text(encoding="utf-8")
        line = "MODEL_WEIGHTS_PATH=models/aoi_unified.pt"
        if "MODEL_WEIGHTS_PATH=" in text:
            import re

            text = re.sub(r"^MODEL_WEIGHTS_PATH=.*$", line, text, flags=re.MULTILINE)
        else:
            text = text.rstrip() + "\n" + line + "\n"
        env_file.write_text(text, encoding="utf-8")
        print(f"Patched .env -> {line}")
    elif not unified.is_file():
        print("WARN: models/aoi_unified.pt missing — portable will use fallback unless datasets are active")

    storage = root / "storage"
    for sub in ("images", "training", "golden_boards"):
        (storage / sub).mkdir(parents=True, exist_ok=True)
    print(f"storage/ ready under {storage}")

    cert = root / "certs" / "cert.pem"
    key = root / "certs" / "key.pem"
    if not cert.is_file() or not key.is_file():
        print("Generating dev TLS certificates...")
        subprocess.run(
            [sys.executable, "-m", "scripts.generate_dev_https_certs"],
            cwd=root,
            check=True,
        )

    db = root / "aoi.db"
    if not db.is_file():
        print("Initializing aoi.db...")
        subprocess.run(
            [sys.executable, "-m", "scripts.init_db"],
            cwd=root,
            check=True,
        )
    else:
        print(f"Using existing {db.name}")

    print("CI portable bundle prerequisites OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
