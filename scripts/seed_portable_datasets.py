"""Регистрация ``models/datasets/1..7/weights.pt`` в ``aoi.db`` для portable-сборки."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASETS_ROOT = ROOT / "models" / "datasets"


def main() -> int:
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Dataset

    created = updated = 0
    with SessionLocal() as db:
        for sub in sorted(DATASETS_ROOT.iterdir(), key=lambda p: p.name):
            if not sub.is_dir() or not sub.name.isdigit():
                continue
            weights = sub / "weights.pt"
            if not weights.is_file() or weights.stat().st_size == 0:
                continue
            ds_id = int(sub.name)
            rel = f"models/datasets/{ds_id}/weights.pt"
            name = f"Датасет {ds_id}"
            size = weights.stat().st_size
            row = db.get(Dataset, ds_id)
            if row is None:
                row = db.execute(select(Dataset).where(Dataset.name == name)).scalar_one_or_none()
            if row is None:
                db.add(
                    Dataset(
                        id=ds_id,
                        name=name,
                        description="Веса из portable / Release (models bundle)",
                        file_path=rel,
                        file_size=size,
                        original_filename="weights.pt",
                        is_active=False,
                    )
                )
                created += 1
                print(f"ADD  id={ds_id} {rel}")
            else:
                row.file_path = rel
                row.file_size = size
                row.original_filename = row.original_filename or "weights.pt"
                updated += 1
                print(f"UPD  id={row.id} {rel}")
        db.commit()

    if not created and not updated:
        print("WARN: no models/datasets/*/weights.pt found", file=sys.stderr)
        return 0
    print(f"OK: datasets seeded (created={created}, updated={updated})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
