"""Windows launcher for AOI-Web HTTPS mode.

This file is intended to be packaged with PyInstaller into a small exe.
It does not bundle the whole ML stack. Instead, it runs the project's
``.venv\\Scripts\\python.exe`` and starts uvicorn from the working project tree.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PORT = "8000"


def _candidate_roots() -> list[Path]:
    exe = Path(sys.executable).resolve()
    here = Path(__file__).resolve()
    cwd = Path.cwd().resolve()
    return [
        cwd,
        exe.parent,
        exe.parent.parent,
        here.parent.parent,
    ]


def find_project_root() -> Path:
    seen: set[Path] = set()
    for root in _candidate_roots():
        if root in seen:
            continue
        seen.add(root)
        if (root / "app" / "main.py").is_file() and (root / ".venv" / "Scripts" / "python.exe").is_file():
            return root
    raise SystemExit(
        "ERROR: project root not found. Put this exe into the project root "
        "or dist/ under the project root."
    )


def run_checked(cmd: list[str], *, cwd: Path) -> None:
    code = subprocess.call(cmd, cwd=str(cwd))
    if code != 0:
        raise SystemExit(code)


def get_public_base_url(py: Path, root: Path) -> str | None:
    if os.environ.get("PUBLIC_BASE_URL", "").strip():
        return os.environ["PUBLIC_BASE_URL"].strip()

    proc = subprocess.run(
        [
            str(py),
            "-m",
            "scripts.ensure_public_base_url",
            "--scheme",
            "https",
            "--port",
            PORT,
        ],
        cwd=str(root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=None,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)
    url = (proc.stdout or "").strip().splitlines()
    return url[-1].strip() if url else None


def main() -> int:
    root = find_project_root()
    os.chdir(root)
    py = root / ".venv" / "Scripts" / "python.exe"

    if not (root / "certs" / "cert.pem").is_file():
        print("Generating dev TLS certificates...")
        run_checked([str(py), "-m", "scripts.generate_dev_https_certs"], cwd=root)
        print()

    public_base_url = get_public_base_url(py, root)
    if public_base_url:
        os.environ["PUBLIC_BASE_URL"] = public_base_url

    print("HTTPS: https://localhost:8000/")
    if public_base_url:
        print(f"Phone/meta: {public_base_url.rstrip('/')}/")
    print("Stop: Ctrl+C or close this window.")
    print()

    return subprocess.call(
        [
            str(py),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            PORT,
            "--ssl-keyfile",
            "certs\\key.pem",
            "--ssl-certfile",
            "certs\\cert.pem",
        ],
        cwd=str(root),
        env=os.environ.copy(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
