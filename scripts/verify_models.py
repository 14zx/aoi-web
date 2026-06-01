"""Проверка наличия файлов весов по ``models/manifest.yaml``."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
MANIFEST = MODELS / "manifest.yaml"


def main() -> int:
    if not MANIFEST.is_file():
        print(f"Нет манифеста: {MANIFEST}", file=sys.stderr)
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
            print(f"НЕТ  {rel}  (обязательный)")
        else:
            missing_optional.append(rel)
            print(f"—    {rel}  (опционально)")

    print()
    print(f"Найдено файлов: {ok}")
    if missing_optional:
        print(f"Опционально отсутствует: {len(missing_optional)}")
    if missing_required:
        print(f"Обязательных нет: {len(missing_required)}", file=sys.stderr)
        print("См. models/README.md — архив Release или scripts.download_*", file=sys.stderr)
        return 1

    print("Проверка пройдена.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
