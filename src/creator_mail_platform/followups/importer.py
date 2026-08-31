from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select

from ..analysis.reply_analyzer import cached_entries
from ..core.db import get_engine, session_scope
from ..core.models import ActivityContact, CampaignResponseProfile
from ..core.utils import extract_send_address, normalize_email_key
from .templates import build_rejection_copy


BLOCKED_EMAIL_STATUSES = ("invalid", "suppressed")


def initialize_response_profile_schema() -> None:
    """Create only the compact response-profile table."""
    CampaignResponseProfile.__table__.create(bind=get_engine(), checkfirst=True)


def prepare_response_profiles(
    *,
    batch_from: int = 1,
    batch_to: int | None = None,
    limit: int | None = None,
    apply: bool = False,
) -> dict[str, object]:
    """Create one rejection conversation row for every genuine responder."""
    if batch_from < 1:
        raise ValueError("batch_from must be positive")
    if batch_to is not None and batch_to < batch_from:
        raise ValueError("batch_to must be greater than or equal to batch_from")

    cache = cached_entries()
    contacts = _load_candidates(
        batch_from=batch_from,
        batch_to=batch_to,
        cached_ids=set(cache),
        limit=limit,
    )
    ready: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for contact in contacts:
        recipient = extract_send_address(contact.email)
        mailbox = normalize_email_key(contact.sender_email or "")
        if recipient is None:
            rejected.append({"creator_id": contact.creator_id, "reason": "invalid_recipient"})
            continue
        if not mailbox:
            rejected.append({"creator_id": contact.creator_id, "reason": "missing_sender_mailbox"})
            continue

        cache_entry = cache.get(contact.creator_id, {})
        reply_snapshot = _reply_snapshot(contact, cache_entry)
        copy = build_rejection_copy(contact.handle, contact.language)
        messages = _initial_messages(
            contact,
            reply_snapshot=reply_snapshot,
            rejection_subject=copy.subject,
            rejection_body=copy.body_text,
        )
        analysis = cache_entry.get("analysis")
        if not isinstance(analysis, dict):
            analysis = {}
        ready.append(
            {
                "creator_id": contact.creator_id,
                "batch_no": contact.batch_no,
                "mailbox_email": mailbox,
                "recipient_email": recipient,
                "messages_json": messages,
                "analysis_json": {
                    "initial_response": analysis,
                    "creator_profile": {
                        "platform": contact.platform,
                        "handle": contact.handle,
                        "country": contact.country,
                        "language": contact.language,
                        "primary_category": contact.primary_category,
                        "profile_bio": contact.profile_bio,
                        "analysis_note": contact.analysis_note,
                        "follower_count": contact.follower_count,
                    },
                },
            }
        )

    result: dict[str, object] = {
        "batch_from": batch_from,
        "batch_to": batch_to,
        "selected": len(contacts),
        "ready": len(ready),
        "rejected": len(rejected),
        "rejection_details": rejected[:100],
        "applied": apply,
        "previews": [
            {
                "creator_id": row["creator_id"],
                "mailbox_email": row["mailbox_email"],
                "subject": row["messages_json"][-1]["subject"],
                "body_text": row["messages_json"][-1]["body_text"],
            }
            for row in ready[:5]
        ],
    }
    if not apply:
        return result

    initialize_response_profile_schema()
    now = datetime.now(UTC)
    created = 0
    preserved = 0
    with session_scope() as session:
        for row in ready:
            creator_id = str(row["creator_id"])
            profile = session.get(CampaignResponseProfile, creator_id, with_for_update=True)
            if profile is None:
                messages = list(row["messages_json"])
                session.add(
                    CampaignResponseProfile(
                        creator_id=creator_id,
                        batch_no=int(row["batch_no"]),
                        mailbox_email=str(row["mailbox_email"]),
                        recipient_email=str(row["recipient_email"]),
                        messages_json=messages,
                        analysis_json=dict(row["analysis_json"]),
                        status="draft",
                        last_message_at=_last_occurred_at(messages),
                        created_at=now,
                        updated_at=now,
                    )
                )
                created += 1
                continue

            # An existing conversation is append-only. Only an unsent draft
            # can receive refreshed source data and rejection copy.
            if profile.status == "draft":
                profile.batch_no = int(row["batch_no"])
                profile.mailbox_email = str(row["mailbox_email"])
                profile.recipient_email = str(row["recipient_email"])
                profile.analysis_json = dict(row["analysis_json"])
                profile.messages_json = _refresh_draft(
                    list(profile.messages_json or []),
                    list(row["messages_json"]),
                )
                profile.last_message_at = _last_occurred_at(profile.messages_json)
                profile.updated_at = now
            else:
                preserved += 1

    result["created"] = created
    result["preserved_existing_conversations"] = preserved
    return result


def _load_candidates(
    *,
    batch_from: int,
    batch_to: int | None,
    cached_ids: set[str],
    limit: int | None,
) -> list[ActivityContact]:
    response_conditions = [
        ActivityContact.reply_type == "human_reply",
        ActivityContact.form_submitted_at.is_not(None),
    ]
    if cached_ids:
        response_conditions.append(ActivityContact.creator_id.in_(cached_ids))
    statement = (
        select(ActivityContact)
        .where(
            ActivityContact.batch_no >= batch_from,
            ActivityContact.send_status == "smtp_accepted",
            ActivityContact.email_status.not_in(BLOCKED_EMAIL_STATUSES),
            or_(*response_conditions),
        )
        .order_by(ActivityContact.batch_no, ActivityContact.creator_id)
    )
    if batch_to is not None:
        statement = statement.where(ActivityContact.batch_no <= batch_to)
    if limit is not None:
        statement = statement.limit(limit)
    with session_scope() as session:
        return list(session.scalars(statement))


def _reply_snapshot(
    contact: ActivityContact,
    cache_entry: dict[str, object],
) -> dict[str, object]:
    if contact.reply_type == "human_reply":
        return {
            "reply_subject": contact.reply_subject,
            "reply_body_text": contact.reply_body_text,
            "last_reply_at": contact.last_reply_at,
        }
    snapshot = cache_entry.get("reply_snapshot")
    return dict(snapshot) if isinstance(snapshot, dict) else {}


def _initial_messages(
    contact: ActivityContact,
    *,
    reply_snapshot: dict[str, object],
    rejection_subject: str,
    rejection_body: str,
) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = [
        {
            "source_key": f"activity:{contact.creator_id}:initial_outreach",
            "direction": "outbound",
            "message_kind": "initial_outreach",
            "message_id": _lower(contact.outbound_message_id),
            "in_reply_to": None,
            "references": [],
            "from_email": contact.sender_email,
            "to_email": contact.email,
            "subject": contact.sent_subject,
            "body_text": contact.sent_body_text,
            "occurred_at": _iso(contact.sent_at),
            "delivery_status": contact.send_status,
            "semantic_intent": None,
            "metadata": {
                "brief_code": contact.brief_code,
                "offered_brief_codes": contact.offered_brief_codes or [],
            },
        }
    ]
    if any(reply_snapshot.get(key) for key in ("reply_subject", "reply_body_text", "last_reply_at")):
        messages.append(
            {
                "source_key": f"activity:{contact.creator_id}:initial_creator_reply",
                "direction": "inbound",
                "message_kind": "initial_creator_reply",
                "message_id": None,
                "in_reply_to": _lower(contact.outbound_message_id),
                "references": [_lower(contact.outbound_message_id)]
                if contact.outbound_message_id
                else [],
                "from_email": contact.email,
                "to_email": contact.sender_email,
                "subject": reply_snapshot.get("reply_subject"),
                "body_text": reply_snapshot.get("reply_body_text"),
                "occurred_at": _iso(reply_snapshot.get("last_reply_at")),
                "delivery_status": "received",
                "semantic_intent": "human_reply",
                "metadata": {},
            }
        )
    if contact.form_submitted_at is not None:
        messages.append(
            {
                "source_key": f"form:{contact.creator_id}:creator_pass",
                "direction": "inbound",
                "message_kind": "creator_pass_submission",
                "message_id": None,
                "in_reply_to": None,
                "references": [],
                "from_email": contact.email,
                "to_email": contact.sender_email,
                "subject": "Creator Pass submitted",
                "body_text": None,
                "occurred_at": _iso(contact.form_submitted_at),
                "delivery_status": "received",
                "semantic_intent": "form_submission",
                "metadata": contact.form_response_json or {},
            }
        )
    messages.append(
        {
            "source_key": f"followup:{contact.creator_id}:rejection",
            "direction": "outbound",
            "message_kind": "rejection_notice",
            "message_id": None,
            "in_reply_to": None,
            "references": [],
            "from_email": contact.sender_email,
            "to_email": contact.email,
            "subject": rejection_subject,
            "body_text": rejection_body,
            "occurred_at": None,
            "delivery_status": "draft",
            "semantic_intent": None,
            "metadata": {},
        }
    )
    return messages


def _refresh_draft(
    existing: list[dict[str, object]],
    fresh: list[dict[str, object]],
) -> list[dict[str, object]]:
    rejection = next(
        (message for message in existing if message.get("message_kind") == "rejection_notice"),
        None,
    )
    if rejection and rejection.get("delivery_status") not in {"draft", "temporary_failed"}:
        return existing
    return fresh


def _last_occurred_at(messages: list[dict[str, object]]) -> datetime | None:
    values = [_parse_datetime(message.get("occurred_at")) for message in messages]
    return max((value for value in values if value is not None), default=None)


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _lower(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text or None
