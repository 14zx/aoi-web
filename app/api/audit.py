"""Журнал аудита: администратор — все записи; руководитель — только сотрудники."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AuditLog, User, UserRole
from ..schemas.audit import AuditLogListOut, AuditLogOut
from .deps import require_manager


router = APIRouter(prefix="/api/audit", tags=["Журнал аудита"])

_EXPORT_MAX_ROWS = 50_000


def _parse_day_start(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value.strip()[:10], "%Y-%m-%d")


def _parse_day_end(value: str | None) -> datetime | None:
    if not value:
        return None
    start = _parse_day_start(value)
    if start is None:
        return None
    return start + timedelta(days=1) - timedelta(microseconds=1)


def _operator_ids(db: Session) -> list[int]:
    return list(
        db.scalars(select(User.id).where(User.role == UserRole.OPERATOR)).all()
    )


def _audit_filters(
    db: Session,
    actor: User,
    *,
    user_id: int | None,
    from_date: str | None,
    to_date: str | None,
    action: str | None,
) -> list:
    clauses: list = []
    if actor.role == UserRole.ADMIN:
        if user_id is not None:
            clauses.append(AuditLog.user_id == user_id)
    else:
        op_ids = _operator_ids(db)
        if user_id is not None:
            if user_id not in op_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Руководитель может просматривать только журнал сотрудников",
                )
            clauses.append(AuditLog.user_id == user_id)
        elif op_ids:
            clauses.append(AuditLog.user_id.in_(op_ids))
        else:
            clauses.append(AuditLog.user_id == -1)

    day_from = _parse_day_start(from_date)
    if day_from is not None:
        clauses.append(AuditLog.created_at >= day_from)
    day_to = _parse_day_end(to_date)
    if day_to is not None:
        clauses.append(AuditLog.created_at <= day_to)
    if action:
        clauses.append(AuditLog.action == action.strip())
    return clauses


def _rows_query(clauses: list):
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    if clauses:
        stmt = stmt.where(*clauses)
    return stmt


@router.get("", response_model=AuditLogListOut, summary="Список записей журнала аудита")
def list_audit_logs(
    user_id: int | None = Query(default=None, ge=1),
    from_date: str | None = Query(default=None, description="YYYY-MM-DD"),
    to_date: str | None = Query(default=None, description="YYYY-MM-DD"),
    action: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    actor: User = Depends(require_manager),
    db: Session = Depends(get_db),
) -> AuditLogListOut:
    clauses = _audit_filters(
        db,
        actor,
        user_id=user_id,
        from_date=from_date,
        to_date=to_date,
        action=action,
    )
    count_stmt = select(func.count()).select_from(AuditLog)
    if clauses:
        count_stmt = count_stmt.where(*clauses)
    total = int(db.scalar(count_stmt) or 0)

    offset = (page - 1) * page_size
    rows = db.scalars(_rows_query(clauses).offset(offset).limit(page_size)).all()
    return AuditLogListOut(
        items=[AuditLogOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/export.csv", summary="Выгрузка журнала аудита в CSV")
def export_audit_logs_csv(
    user_id: int | None = Query(default=None, ge=1),
    from_date: str | None = Query(default=None, description="YYYY-MM-DD"),
    to_date: str | None = Query(default=None, description="YYYY-MM-DD"),
    action: str | None = Query(default=None, max_length=64),
    actor: User = Depends(require_manager),
    db: Session = Depends(get_db),
) -> Response:
    clauses = _audit_filters(
        db,
        actor,
        user_id=user_id,
        from_date=from_date,
        to_date=to_date,
        action=action,
    )
    rows = db.scalars(_rows_query(clauses).limit(_EXPORT_MAX_ROWS)).all()

    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(["ID", "Дата и время", "Пользователь", "Действие", "Объект", "Подробности", "IP"])
    for row in rows:
        writer.writerow(
            [
                row.id,
                row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
                row.username or "",
                row.action,
                row.target or "",
                (row.details or "").replace("\r\n", " ").replace("\n", " "),
                row.ip_address or "",
            ]
        )

    filename = f"audit-log-{datetime.now().strftime('%Y%m%d-%H%M')}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
