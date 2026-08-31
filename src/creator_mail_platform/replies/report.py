from __future__ import annotations

from sqlalchemy import select

from ..core.db import session_scope
from ..core.models import ActivityContact


def list_replies(
    *,
    batch_no: int | None = None,
    batch_from: int | None = None,
    batch_to: int | None = None,
    reply_type: str | None = "human_reply",
    limit: int = 100,
) -> list[dict[str, object]]:
    _validate_scope(batch_no, batch_from, batch_to, limit)
    statement = (
        select(ActivityContact)
        .where(ActivityContact.last_reply_at.is_not(None))
        .order_by(ActivityContact.last_reply_at.desc())
        .limit(limit)
    )
    if batch_no is not None:
        statement = statement.where(ActivityContact.batch_no == batch_no)
    elif batch_from is not None and batch_to is not None:
        statement = statement.where(
            ActivityContact.batch_no.between(batch_from, batch_to)
        )
    if reply_type:
        statement = statement.where(ActivityContact.reply_type == reply_type)

    with session_scope() as session:
        contacts = list(session.scalars(statement))
    return [
        {
            "batch_no": contact.batch_no,
            "creator_id": contact.creator_id,
            "handle": contact.handle,
            "email": contact.email,
            "sender_email": contact.sender_email,
            "primary_brief_code": contact.brief_code,
            "offered_brief_codes": contact.offered_brief_codes or [],
            "reply_type": contact.reply_type,
            "last_reply_at": (
                contact.last_reply_at.isoformat() if contact.last_reply_at else None
            ),
            "reply_subject": contact.reply_subject,
            "reply_body_text": contact.reply_body_text,
        }
        for contact in contacts
    ]


def _validate_scope(
    batch_no: int | None,
    batch_from: int | None,
    batch_to: int | None,
    limit: int,
) -> None:
    if batch_no is not None and (batch_from is not None or batch_to is not None):
        raise ValueError("Use either --batch or --batch-from/--batch-to")
    if (batch_from is None) != (batch_to is None):
        raise ValueError("--batch-from and --batch-to must be used together")
    if batch_from is not None and batch_to is not None and batch_from > batch_to:
        raise ValueError("--batch-from cannot be greater than --batch-to")
    if limit < 1:
        raise ValueError("--limit must be positive")
