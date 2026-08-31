from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from ..core.config import get_settings
from ..core.db import session_scope
from ..core.models import CampaignResponseProfile


def list_followup_threads(
    *,
    status: str | None = None,
    needs_reply: bool | None = None,
    mailbox_email: str | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    with session_scope() as session:
        mailbox = mailbox_email or get_settings().followup_mailbox_email
        statement = select(CampaignResponseProfile).where(
            func.lower(CampaignResponseProfile.mailbox_email) == mailbox.strip().lower()
        )
        effective_status = "need_reply" if needs_reply is True else status
        if effective_status:
            statement = statement.where(CampaignResponseProfile.status == effective_status)
        elif needs_reply is False:
            statement = statement.where(CampaignResponseProfile.status != "need_reply")
        profiles = list(
            session.scalars(
                statement.order_by(
                    CampaignResponseProfile.updated_at.desc(),
                    CampaignResponseProfile.creator_id,
                ).limit(limit)
            )
        )
    return [_profile_summary(profile) for profile in profiles]


def list_thread_messages(creator_id: str) -> list[dict[str, object]]:
    with session_scope() as session:
        profile = session.get(CampaignResponseProfile, creator_id)
        if profile is None:
            raise ValueError(f"Unknown response profile: {creator_id}")
        messages = list(profile.messages_json or [])
    return sorted(messages, key=lambda item: str(item.get("occurred_at") or ""))


def _profile_summary(profile: CampaignResponseProfile) -> dict[str, object]:
    messages: list[dict[str, Any]] = list(profile.messages_json or [])
    latest = max(messages, key=lambda item: str(item.get("occurred_at") or ""), default={})
    creator = profile.analysis_json.get("creator_profile", {})
    return {
        "creator_id": profile.creator_id,
        "handle": creator.get("handle") if isinstance(creator, dict) else None,
        "batch_no": profile.batch_no,
        "mailbox_email": profile.mailbox_email,
        "recipient_email": profile.recipient_email,
        "status": profile.status,
        "needs_reply": profile.status == "need_reply",
        "message_count": len(messages),
        "latest_message_kind": latest.get("message_kind"),
        "latest_message_summary": str(latest.get("body_text") or latest.get("subject") or "")[:500] or None,
        "last_message_at": profile.last_message_at.isoformat() if profile.last_message_at else None,
    }
