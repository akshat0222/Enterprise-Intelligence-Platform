import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import ActivityLog


def log_activity(
    db: Session,
    user_id: uuid.UUID | None,
    action: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    level: str = "info",
) -> None:
    db.add(
        ActivityLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            level=level,
        )
    )
    db.commit()
