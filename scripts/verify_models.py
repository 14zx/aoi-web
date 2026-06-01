"""Verify model weight files listed in ``models/manifest.yaml``."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
MANIFEST = MODELS / "manifest.yaml"


def main() -> int:
    if not MANIFEST.is_file():
        print(f"ERROR: missing manifest: {MANIFEST}", file=sys.stderr)
        return 1

    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}
    files = data.get("files") or []
    missing_required: list[str] = []
    missing_optional: list[str] = []
    ok = 0

    for entry in files:
        rel = str(entry.get("path") or "").strip()
        if not rel:
            continue
        path = MODELS / rel
        required = bool(entry.get("required"))
        if path.is_file() and path.stat().st_size > 0:
            ok += 1
            print(f"OK   {rel}")
        elif required:
            missing_required.append(rel)
            print(f"MISS {rel}  (required)", file=sys.stderr)
        else:
            missing_optional.append(rel)
            print(f"SKIP {rel}  (optional)")

    print()
    print(f"Found: {ok} file(s)")
    if missing_optional:
        print(f"Optional missing: {len(missing_optional)}")
    if missing_required:
        print(f"Required missing: {len(missing_required)}", file=sys.stderr)
        print("See models/README.md or Release AOI-Web-models-*.zip", file=sys.stderr)
        return 1

    print("verify_models: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
