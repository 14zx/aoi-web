"""HTTP-маршруты приложения."""

from .audit import router as audit_router
from .auth import router as auth_router
from .datasets import router as datasets_router
from .devices import router as devices_router
from .golden_boards import router as golden_boards_router
from .inspections import router as inspections_router
from .pipeline import router as pipeline_router
from .meta import router as meta_router
from .settings import router as settings_router
from .stats import router as stats_router
from .users import router as users_router

__all__ = [
    "audit_router",
    "auth_router",
    "datasets_router",
    "devices_router",
    "golden_boards_router",
    "inspections_router",
    "pipeline_router",
    "meta_router",
    "settings_router",
    "stats_router",
    "users_router",
]
