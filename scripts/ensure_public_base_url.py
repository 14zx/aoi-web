"""Печатает PUBLIC_BASE_URL для текущего LAN (одна строка в stdout) или ничего.

Вызывается из run.bat / run_https.bat до uvicorn: если в окружении и .env
нет явного PUBLIC_BASE_URL, подставляет http(s)://<LAN-IP>:<port> для ссылок на телефон.

Правило: не подменяем уже заданное значение (переменная окружения или непустая строка в .env).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _public_base_url_from_dotenv(env_path: Path) -> str | None:
    if not env_path.is_file():
        return None
    try:
        text = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not line.upper().startswith("PUBLIC_BASE_URL"):
            continue
        _, _, rest = line.partition("=")
        val = rest.strip().strip('"').strip("'")
        return val if val else None
    return None


def main() -> int:
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
        try:
            sys.stderr.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(description="PUBLIC_BASE_URL for LAN phone links (stdout)")
    parser.add_argument(
        "--scheme",
        choices=("http", "https"),
        default="https",
        help="Схема в URL (run_https.bat — https, run.bat — http)",
    )
    parser.add_argument("--port", type=int, default=8000, help="Порт uvicorn")
    args = parser.parse_args()

    if os.environ.get("PUBLIC_BASE_URL", "").strip():
        return 0

    env_file = _repo_root() / ".env"
    if _public_base_url_from_dotenv(env_file):
        return 0

    from scripts.lan_ip import get_lan_ipv4

    lan = get_lan_ipv4()
    if not lan:
        print("LAN IPv4 not detected; set PUBLIC_BASE_URL in .env if needed.", file=sys.stderr)
        return 0

    url = f"{args.scheme}://{lan}:{args.port}".rstrip("/")
    print(url)
    print(f"Using PUBLIC_BASE_URL={url} for this session (phone / QR links).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
