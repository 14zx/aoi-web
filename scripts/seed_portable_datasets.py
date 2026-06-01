"""Register ``models/datasets/7/weights.pt`` in ``aoi.db`` for portable builds."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIMARY_DATASET_ID = 7
WEIGHTS = ROOT / "models" / "datasets" / str(PRIMARY_DATASET_ID) / "weights.pt"
REL_PATH = f"models/datasets/{PRIMARY_DATASET_ID}/weights.pt"


def main() -> int:
    if not WEIGHTS.is_file() or WEIGHTS.stat().st_size == 0:
        print(f"WARN: missing {WEIGHTS}", file=sys.stderr)
        return 0

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Dataset

    name = f"Dataset {PRIMARY_DATASET_ID}"
    size = WEIGHTS.stat().st_size

    with SessionLocal() as db:
        for row in db.execute(select(Dataset)).scalars().all():
            row.is_active = False

        row = db.get(Dataset, PRIMARY_DATASET_ID)
        if row is None:
            row = db.execute(select(Dataset).where(Dataset.name == name)).scalar_one_or_none()
        if row is None:
            db.add(
                Dataset(
                    id=PRIMARY_DATASET_ID,
                    name=name,
                    description="Primary model (portable / Release)",
                    file_path=REL_PATH,
                    file_size=size,
                    original_filename="weights.pt",
                    is_active=True,
                )
            )
            print(f"ADD  id={PRIMARY_DATASET_ID} {REL_PATH} (active)")
        else:
            row.file_path = REL_PATH
            row.file_size = size
            row.is_active = True
            print(f"UPD  id={row.id} {REL_PATH} (active)")
        db.commit()

    print("OK: primary dataset seeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
