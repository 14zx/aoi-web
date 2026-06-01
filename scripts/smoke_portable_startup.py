"""Smoke-test portable exe: wait for uvicorn startup (45 s max).

Used by build_portable_https.bat after PyInstaller.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

OK_MARKER = "Application startup complete"
YOLO_MARKER = "Загружены веса YOLOv8"
FAIL_MARKERS = ("Could not import module", "Traceback", "Error loading ASGI")
TIMEOUT_S = 45


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: smoke_portable_startup.py <exe> <working_dir>", file=sys.stderr)
        return 2

    exe = Path(sys.argv[1]).resolve()
    cwd = Path(sys.argv[2]).resolve()
    if not exe.is_file():
        print(f"ERROR: exe not found: {exe}", file=sys.stderr)
        return 1
    if not cwd.is_dir():
        print(f"ERROR: working dir not found: {cwd}", file=sys.stderr)
        return 1

    tmp = Path(os.environ.get("TEMP", "."))
    out_path = tmp / "aoi_portable_smoke_out.txt"
    err_path = tmp / "aoi_portable_smoke_err.txt"
    out_path.unlink(missing_ok=True)
    err_path.unlink(missing_ok=True)

    with out_path.open("w", encoding="utf-8", errors="replace") as out_f, err_path.open(
        "w", encoding="utf-8", errors="replace"
    ) as err_f:
        smoke_env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
        proc = subprocess.Popen(
            [str(exe)],
            cwd=str(cwd),
            stdout=out_f,
            stderr=err_f,
            env=smoke_env,
        )

        deadline = time.time() + TIMEOUT_S
        ok = False
        while time.time() < deadline:
            blob = ""
            for path in (err_path, out_path):
                if path.exists():
                    blob += path.read_text(encoding="utf-8", errors="replace")
            if OK_MARKER in blob:
                ok = True
                break
            if any(m in blob for m in FAIL_MARKERS):
                break
            if proc.poll() is not None:
                time.sleep(1)
                break
            time.sleep(0.4)

        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()

    print("--- stderr (tail) ---")
    if err_path.exists():
        lines = err_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-40:]:
            print(line)
    print("--- stdout (tail) ---")
    if out_path.exists():
        lines = out_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-20:]:
            print(line)

    blob = ""
    for path in (err_path, out_path):
        if path.exists():
            blob += path.read_text(encoding="utf-8", errors="replace")

    primary = cwd / "_internal" / "models" / "datasets" / "7" / "weights.pt"
    if not primary.is_file():
        primary = cwd / "models" / "datasets" / "7" / "weights.pt"
    if primary.is_file():
        mb = primary.stat().st_size / (1024 * 1024)
        print(f"OK: bundled weights {primary} ({mb:.1f} MiB)")
    else:
        print("WARN: models/datasets/7/weights.pt not in portable folder", file=sys.stderr)

    if ok:
        print("OK: Application startup complete")
        if YOLO_MARKER not in blob:
            print("WARN: YOLO weights were not loaded (check MODEL_WEIGHTS_PATH in .env)", file=sys.stderr)
        else:
            print("OK: YOLO weights loaded")
        return 0

    print("FAIL: smoke test did not see 'Application startup complete'", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
