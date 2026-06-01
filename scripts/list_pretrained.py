"""Служебный скрипт: перечислить содержимое публичных репозиториев HF."""
from huggingface_hub import list_repo_files

REPOS = [
    "ampragatish/yolov8n-pcb-defects-detection",
    "keremberke/yolov8n-pcb-defect-segmentation",
    "avinashhm/welding-defect-yolov8-full-training",
]
for repo in REPOS:
    try:
        files = list_repo_files(repo)
        print("===", repo, "===")
        for f in files:
            print(" ", f)
    except Exception as exc:  # noqa: BLE001
        print("ERR", repo, exc)
