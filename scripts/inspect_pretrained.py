"""Читает метаданные скачанных весов (без запуска инференса)."""

from __future__ import annotations

from pathlib import Path

import torch

BASE = Path(__file__).resolve().parent.parent / "models" / "pretrained"

TARGETS = [
    BASE / "pcb-defects-6cls" / "yolov8n_pcb_defects_6cls.pt",
    BASE / "pcb-defect-seg" / "yolov8n_pcb_defect_seg.pt",
    BASE / "welding-defects" / "yolov8_welding_defects.pt",
]


def describe(path: Path) -> None:
    print("===", path.name)
    ckpt = torch.load(path, map_location="cpu", weights_only=False)
    if isinstance(ckpt, dict):
        model = ckpt.get("model")
        names = getattr(model, "names", None) or ckpt.get("names")
        task = ckpt.get("train_args", {}).get("task") or getattr(model, "task", None)
        imgsz = ckpt.get("train_args", {}).get("imgsz")
        print(f"  task={task}  imgsz={imgsz}")
        print(f"  classes ({len(names) if names else '?'}): {names}")
    else:
        print("  <не dict checkpoint>")


def main() -> None:
    for target in TARGETS:
        try:
            describe(target)
        except Exception as exc:  # noqa: BLE001
            print("ERR", target, exc)


if __name__ == "__main__":
    main()
