"""Сервисы приложения: предобработка, детекция, визуализация, отчёты."""

from . import dataset_manager
from .detector import DEFECT_CLASSES, DetectedDefect, Detector, get_detector
from .preprocessing import apply_detection_preprocess, load_image, preprocess_image
from .reports import generate_csv_report, generate_pdf_report
from .visualization import render_result_image

__all__ = [
    "DEFECT_CLASSES",
    "DetectedDefect",
    "Detector",
    "apply_detection_preprocess",
    "generate_csv_report",
    "generate_pdf_report",
    "get_detector",
    "load_image",
    "preprocess_image",
    "render_result_image",
]
