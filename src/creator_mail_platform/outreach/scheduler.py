from __future__ import annotations

from sqlalchemy import func, or_, select, text, update

from ..core.db import session_scope
from ..core.models import ActivityContact
from ..core.utils import extract_send_address, normalize_email_key
from .sender import (
    BLOCKED_EMAIL_STATUSES,
    ELIGIBLE_SEND_STATUSES,
    load_smtp_accounts,
    validate_smtp_account,
)


def assign_batch_to_sender(
    *,
    batch_no: int,
    sender_email: str,
    apply: bool = False,
) -> dict[str, object]:
    """Preview or bind all send-ready contacts in one batch to one SMTP account."""
    if batch_no < 1:
        raise ValueError("batch_no must be positive")
    sender = normalize_email_key(sender_email)
    if extract_send_address(sender) is None:
        raise ValueError(f"Invalid sender email: {sender_email}")

    account = load_smtp_accounts().get(sender)
    if account is None:
        raise RuntimeError(f"SMTP account {sender} is not enabled in SMTP_ACCOUNTS_FILE")
    if apply:
        validate_smtp_account(account)

    eligible = (
        ActivityContact.batch_no == batch_no,
        *_eligible_filters(),
    )
    content_ready = (
        ActivityContact.sent_subject.is_not(None),
        ActivityContact.sent_body_text.is_not(None),
    )
    missing_content = or_(
        ActivityContact.sent_subject.is_(None),
        ActivityContact.sent_body_text.is_(None),
    )

    with session_scope() as session:
        if apply:
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('creator_mail.assign_batch'))")
            )

        eligible_count = _count(session, *eligible)
        missing_content_count = _count(session, *eligible, missing_content)
        already_assigned = _count(
            session,
            *eligible,
            *content_ready,
            func.lower(ActivityContact.sender_email) == sender,
        )
        conflicting_assignments = _count(
            session,
            *eligible,
            ActivityContact.sender_email.is_not(None),
            func.lower(ActivityContact.sender_email) != sender,
        )
        unassigned_ready = _count(
            session,
            *eligible,
            *content_ready,
            ActivityContact.sender_email.is_(None),
        )

        assigned_now = 0
        applied = False
        if apply and conflicting_assignments == 0:
            result = session.execute(
                update(ActivityContact)
                .where(
                    *eligible,
                    *content_ready,
                    ActivityContact.sender_email.is_(None),
                )
                .values(sender_email=sender)
            )
            assigned_now = int(result.rowcount or 0)
            applied = True

    return {
        "batch_no": batch_no,
        "sender_email": sender,
        "apply_requested": apply,
        "applied": applied,
        "ready": (
            eligible_count > 0
            and conflicting_assignments == 0
            and (unassigned_ready + already_assigned) > 0
        ),
        "eligible": eligible_count,
        "content_ready_unassigned": unassigned_ready,
        "missing_content": missing_content_count,
        "already_assigned_to_sender": already_assigned,
        "conflicting_assignments": conflicting_assignments,
        "assigned_now": assigned_now,
    }


def _eligible_filters():
    return (
        ActivityContact.send_status.in_(ELIGIBLE_SEND_STATUSES),
        ActivityContact.email_status.not_in(BLOCKED_EMAIL_STATUSES),
    )


def _count(session, *filters) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(ActivityContact).where(*filters)
        )
        or 0
    )
