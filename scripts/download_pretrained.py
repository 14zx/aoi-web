"""Скачивание открытых предобученных моделей для АОИ-Web.

Кладёт файлы в ``models/pretrained/<short_name>/`` и печатает итоговые пути —
их можно загрузить через вкладку «Датасеты» в админке.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from huggingface_hub import hf_hub_download

BASE = Path(__file__).resolve().parent.parent / "models" / "pretrained"
BASE.mkdir(parents=True, exist_ok=True)

DOWNLOADS = [
    # (short_name, repo_id, filename_in_repo, destination_filename)
    (
        "pcb-defects-6cls",
        "ampragatish/yolov8n-pcb-defects-detection",
        "best.pt",
        "yolov8n_pcb_defects_6cls.pt",
    ),
    (
        "pcb-defects-6cls",
        "ampragatish/yolov8n-pcb-defects-detection",
        "data.yaml",
        "data.yaml",
    ),
    (
        "pcb-defect-seg",
        "keremberke/yolov8n-pcb-defect-segmentation",
        "best.pt",
        "yolov8n_pcb_defect_seg.pt",
    ),
    (
        "pcb-defect-seg",
        "keremberke/yolov8n-pcb-defect-segmentation",
        "config.json",
        "config.json",
    ),
    (
        "welding-defects",
        "avinashhm/welding-defect-yolov8-full-training",
        "weights/best.pt",
        "yolov8_welding_defects.pt",
    ),
    (
        "welding-defects",
        "avinashhm/welding-defect-yolov8-full-training",
        "args.yaml",
        "args.yaml",
    ),
]


def main() -> None:
    for short, repo, src, dst in DOWNLOADS:
        target_dir = BASE / short
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / dst
        if target.exists() and target.stat().st_size > 0:
            print(f"SKIP {target}  ({target.stat().st_size / 1024:.1f} КБ)")
            continue
        print(f"GET  {repo}::{src} -> {target}")
        cached = hf_hub_download(repo_id=repo, filename=src)
        shutil.copyfile(cached, target)
        print(f"  OK  {target.stat().st_size / 1024:.1f} КБ")


if __name__ == "__main__":
    main()
