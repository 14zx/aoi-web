"""ORM-модели приложения."""

from .app_setting import AppSetting
from .audit import AuditLog
from .dataset import Dataset
from .device import Device
from .golden_board import GoldenBoardProfile
from .inspection import Defect, Inspection, InspectionStatus
from .user import LoginAttempt, User, UserRole

__all__ = [
    "AppSetting",
    "AuditLog",
    "Dataset",
    "Defect",
    "Device",
    "GoldenBoardProfile",
    "Inspection",
    "InspectionStatus",
    "LoginAttempt",
    "User",
    "UserRole",
]
