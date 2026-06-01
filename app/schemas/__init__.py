"""Pydantic-схемы запросов и ответов."""

from .auth import LoginRequest, TokenPayload, TokenResponse
from .dataset import DatasetOut
from .device import (
    DeviceCommand,
    DeviceCreate,
    DeviceOut,
    DeviceStatusIn,
    DeviceStatusOut,
    DeviceUpdate,
)
from .inspection import (
    CONFIRM_PURGE_ALL_INSPECTIONS,
    DailyStat,
    DefectClassStat,
    DefectOut,
    DefectReviewIn,
    InspectionDetailOut,
    InspectionListItem,
    InspectionReviewIn,
    InspectionStatsOut,
    LiveDetectionResult,
    OperatorStats,
    PurgeAllInspectionsIn,
    PurgeAllInspectionsOut,
    WeekdayStat,
)
from .class_semantics import ClassSemanticEntry, ClassSemanticsOut, ClassSemanticsUpdate
from .settings import SettingsOut, SettingsUpdate
from .user import (
    UserCreate,
    UserOut,
    UserPasswordChange,
    UserPasswordSet,
    UserUpdate,
)

__all__ = [
    "ClassSemanticEntry",
    "ClassSemanticsOut",
    "ClassSemanticsUpdate",
    "DailyStat",
    "DatasetOut",
    "DefectClassStat",
    "DefectOut",
    "DefectReviewIn",
    "DeviceCommand",
    "DeviceCreate",
    "DeviceOut",
    "DeviceStatusIn",
    "DeviceStatusOut",
    "DeviceUpdate",
    "InspectionDetailOut",
    "InspectionListItem",
    "InspectionReviewIn",
    "InspectionStatsOut",
    "CONFIRM_PURGE_ALL_INSPECTIONS",
    "PurgeAllInspectionsIn",
    "PurgeAllInspectionsOut",
    "LiveDetectionResult",
    "LoginRequest",
    "OperatorStats",
    "SettingsOut",
    "SettingsUpdate",
    "TokenPayload",
    "TokenResponse",
    "UserCreate",
    "UserOut",
    "UserPasswordChange",
    "UserPasswordSet",
    "UserUpdate",
    "WeekdayStat",
]
