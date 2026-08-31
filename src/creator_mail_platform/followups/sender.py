from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
import time
from typing import Any

from sqlalchemy import select

from ..core.db import get_session_factory, session_scope
from ..core.models import CampaignResponseProfile
from ..core.utils import extract_send_address, normalize_email_key
from ..outreach.sender import (
    DeliveryResult,
    SmtpAccount,
    _close_smtp,
    _deliver_message,
    _new_outbound_message_id,
    _open_smtp,
    _set_message_content,
    _validate_execution_guard,
    get_smtp_account,
)
from .generator import is_rejection_eligible


RETRYABLE_DELIVERY_STATUSES = {"draft", "temporary_failed"}


@dataclass(frozen=True)
class ClaimedFollowup:
    creator_id: str
    recipient: str
    mailbox_email: str
    message_id: str
    message_kind: str
    subject: str
    body_text: str
    in_reply_to: str | None
    references: tuple[str, ...]


def send_rejections(
    *,
    limit: int | None = None,
    execute: bool = False,
    parallel_accounts: int = 10,
    sender_email: str | None = None,
) -> dict[str, object]:
    """Preview or send rejection drafts as brand-new email threads."""
    if parallel_accounts < 1 or parallel_accounts > 10:
        raise ValueError("parallel_accounts must be between 1 and 10")
    if execute:
        _validate_execution_guard(execute=True)
    with session_scope() as session:
        statement = (
            select(CampaignResponseProfile)
            .where(CampaignResponseProfile.status == "draft")
            .order_by(
                CampaignResponseProfile.mailbox_email,
                CampaignResponseProfile.created_at,
            )
        )
        profiles = list(session.scalars(statement))
    ready = [
        (profile, draft)
        for profile in profiles
        if is_rejection_eligible(profile)
        if (
            draft := _find_message(
                list(profile.messages_json or []),
                kind="rejection_notice",
                delivery_statuses=RETRYABLE_DELIVERY_STATUSES,
            )
        )
        is not None
        and bool((draft.get("metadata") or {}).get("generated_by"))
    ]
    if limit is not None:
        ready = ready[:limit]
    sender_override = normalize_email_key(sender_email) if sender_email else None
    result: dict[str, object] = {
        "selected": len(ready),
        "execute": execute,
        "sender_email": sender_override,
        "dry_run": 0 if execute else len(ready),
        **_empty_delivery_counts(),
        "previews": [
            {
                "creator_id": profile.creator_id,
                "mailbox_email": sender_override or profile.mailbox_email,
                "recipient_email": profile.recipient_email,
                "subject": draft.get("subject"),
                "body_text": draft.get("body_text"),
            }
            for profile, draft in ready[:10]
        ],
    }
    if not execute or not ready:
        return result
    grouped: dict[str, list[str]] = {}
    for profile, _ in ready:
        mailbox = sender_override or normalize_email_key(profile.mailbox_email)
        grouped.setdefault(mailbox, []).append(
            profile.creator_id
        )
    worker_jobs: list[tuple[SmtpAccount, list[str], bool]] = []
    for mailbox, creator_ids in grouped.items():
        account = get_smtp_account(mailbox, require_credentials=True)
        for index in range(min(account.max_parallel_sends, len(creator_ids))):
            chunk = creator_ids[index :: account.max_parallel_sends]
            if chunk:
                worker_jobs.append((account, chunk, sender_override is not None))
    totals = _empty_delivery_counts()
    mailboxes = list(grouped)
    for start in range(0, len(mailboxes), parallel_accounts):
        active = set(mailboxes[start : start + parallel_accounts])
        jobs = [
            job for job in worker_jobs
            if normalize_email_key(job[0].from_email) in active
        ]
        if not jobs:
            continue
        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            for counts in executor.map(lambda item: _send_profile_slice(*item), jobs):
                for key in totals:
                    totals[key] += counts[key]
    result.update(totals)
    return result


def preview_thread_reply(
    creator_id: str, *, body_text: str, subject: str | None = None
) -> dict[str, object]:
    with session_scope() as session:
        profile = session.get(CampaignResponseProfile, creator_id)
        if profile is None:
            raise ValueError(f"Unknown response profile: {creator_id}")
        inbound = _latest_inbound(list(profile.messages_json or []))
        if inbound is None:
            raise RuntimeError("This creator has no inbound message to reply to")
        return {
            "creator_id": creator_id,
            "mailbox_email": profile.mailbox_email,
            "recipient_email": profile.recipient_email,
            "subject": subject or _reply_subject(_text(inbound.get("subject"))),
            "body_text": body_text,
            "in_reply_to": inbound.get("message_id"),
        }


def send_thread_reply(
    creator_id: str,
    *,
    body_text: str,
    subject: str | None = None,
    execute: bool = False,
) -> dict[str, object]:
    preview = preview_thread_reply(creator_id, body_text=body_text, subject=subject)
    if not execute:
        return {**preview, "execute": False, "outcome": "dry_run"}
    _validate_execution_guard(execute=True)
    account = get_smtp_account(str(preview["mailbox_email"]), require_credentials=True)
    session = get_session_factory()()
    connection = None
    try:
        claimed = _claim_manual_reply(
            session, creator_id, body_text=body_text, subject=subject, account=account
        )
        if claimed is None:
            raise RuntimeError("Creator reply is no longer sendable")
        connection = _open_smtp(account)
        outcome = _finalize_followup(
            session, claimed, _deliver_message(connection, _build_message(claimed))
        )
        return {
            "creator_id": creator_id,
            "execute": True,
            "outcome": outcome,
            "message_id": claimed.message_id,
        }
    finally:
        session.close()
        if connection is not None:
            _close_smtp(connection)


def read_body_file(path: Path) -> str:
    body = path.read_text(encoding="utf-8").strip()
    if not body:
        raise ValueError("Reply body file is empty")
    return body


def _send_profile_slice(
    account: SmtpAccount,
    creator_ids: list[str],
    allow_mailbox_override: bool = False,
) -> dict[str, int]:
    counts = _empty_delivery_counts()
    session = get_session_factory()()
    connection = None
    messages_on_connection = 0
    try:
        for creator_id in creator_ids:
            if connection is None:
                connection = _open_smtp(account)
                messages_on_connection = 0
            claimed = _claim_rejection(
                session,
                creator_id,
                account=account,
                allow_mailbox_override=allow_mailbox_override,
            )
            session.expunge_all()
            if claimed is None:
                counts["skipped"] += 1
                continue
            delivery = _deliver_message(connection, _build_message(claimed))
            counts[_finalize_followup(session, claimed, delivery)] += 1
            session.expunge_all()
            messages_on_connection += 1
            time.sleep(1.0 / account.rate_per_second)
            if delivery.connection_broken:
                _close_smtp(connection)
                connection = None
                break
            if messages_on_connection >= account.messages_per_connection:
                _close_smtp(connection)
                connection = None
    finally:
        session.close()
        if connection is not None:
            _close_smtp(connection)
    return counts


def _claim_rejection(
    session: Any,
    creator_id: str,
    *,
    account: SmtpAccount,
    allow_mailbox_override: bool = False,
) -> ClaimedFollowup | None:
    with session.begin():
        profile = session.get(CampaignResponseProfile, creator_id, with_for_update=True)
        if profile is None or profile.status != "draft":
            return None
        mailbox_changed = (
            normalize_email_key(profile.mailbox_email)
            != normalize_email_key(account.from_email)
        )
        if mailbox_changed and not allow_mailbox_override:
            return None
        if mailbox_changed:
            profile.mailbox_email = normalize_email_key(account.from_email)
        messages = deepcopy(profile.messages_json or [])
        draft = _find_message(
            messages,
            kind="rejection_notice",
            delivery_statuses=RETRYABLE_DELIVERY_STATUSES,
        )
        if draft is None:
            return None
        recipient = extract_send_address(profile.recipient_email)
        if recipient is None:
            draft["delivery_status"] = "hard_failed"
            draft["status_reason"] = "invalid_recipient"
            profile.messages_json = list(messages)
            profile.status = "closed"
            profile.updated_at = datetime.now(UTC)
            return None
        message_id = _text(draft.get("message_id")) or _new_outbound_message_id(
            account.from_email
        )
        draft["message_id"] = message_id.lower()
        draft["delivery_status"] = "sending"
        draft["status_reason"] = "smtp_in_progress"
        profile.messages_json = list(messages)
        profile.status = "review"
        profile.updated_at = datetime.now(UTC)
        return ClaimedFollowup(
            creator_id=creator_id,
            recipient=recipient,
            mailbox_email=account.from_email,
            message_id=message_id,
            message_kind="rejection_notice",
            subject=_text(draft.get("subject")) or "An update from COOJOY",
            body_text=_text(draft.get("body_text")) or "",
            in_reply_to=None,
            references=(),
        )


def _claim_manual_reply(
    session: Any,
    creator_id: str,
    *,
    body_text: str,
    subject: str | None,
    account: SmtpAccount,
) -> ClaimedFollowup | None:
    with session.begin():
        profile = session.get(CampaignResponseProfile, creator_id, with_for_update=True)
        if profile is None or profile.status != "need_reply":
            return None
        if normalize_email_key(profile.mailbox_email) != normalize_email_key(account.from_email):
            return None
        messages = deepcopy(profile.messages_json or [])
        inbound = _latest_inbound(messages)
        if inbound is None or not inbound.get("message_id"):
            raise RuntimeError("Latest inbound message has no Message-ID; manual review is required")
        inbound_id = str(inbound["message_id"]).lower()
        references = tuple(
            dict.fromkeys([*_string_list(inbound.get("references")), inbound_id])
        )
        message_id = _new_outbound_message_id(account.from_email)
        message = {
            "source_key": f"smtp:{message_id.lower()}",
            "direction": "outbound",
            "message_kind": "followup_reply",
            "message_id": message_id.lower(),
            "in_reply_to": inbound_id,
            "references": list(references),
            "from_email": account.from_email,
            "to_email": profile.recipient_email,
            "subject": subject or _reply_subject(_text(inbound.get("subject"))),
            "body_text": body_text,
            "occurred_at": None,
            "delivery_status": "sending",
            "status_reason": "smtp_in_progress",
            "semantic_intent": None,
            "metadata": {},
        }
        messages.append(message)
        profile.messages_json = messages
        profile.status = "review"
        profile.updated_at = datetime.now(UTC)
        return ClaimedFollowup(
            creator_id=creator_id,
            recipient=profile.recipient_email,
            mailbox_email=account.from_email,
            message_id=message_id,
            message_kind="followup_reply",
            subject=str(message["subject"]),
            body_text=body_text,
            in_reply_to=inbound_id,
            references=references,
        )


def _build_message(claimed: ClaimedFollowup) -> EmailMessage:
    message = EmailMessage()
    message["From"] = claimed.mailbox_email
    message["To"] = claimed.recipient
    message["Subject"] = claimed.subject
    message["Date"] = format_datetime(datetime.now(UTC))
    message["Message-ID"] = claimed.message_id
    if claimed.in_reply_to:
        message["In-Reply-To"] = claimed.in_reply_to
    if claimed.references:
        message["References"] = " ".join(claimed.references)
    _set_message_content(message, claimed.body_text)
    return message


def _finalize_followup(
    session: Any, claimed: ClaimedFollowup, delivery: DeliveryResult
) -> str:
    with session.begin():
        profile = session.get(CampaignResponseProfile, claimed.creator_id, with_for_update=True)
        if profile is None:
            raise RuntimeError("Response profile changed before SMTP finalization")
        messages = deepcopy(profile.messages_json or [])
        message = next(
            (
                item for item in reversed(messages)
                if str(item.get("message_id") or "").lower() == claimed.message_id.lower()
            ),
            None,
        )
        if message is None:
            raise RuntimeError("Outbound follow-up message changed before finalization")
        now = datetime.now(UTC)
        profile.updated_at = now
        if delivery.outcome == "accepted":
            message.update(
                delivery_status="smtp_accepted",
                smtp_code=250,
                status_reason=None,
                occurred_at=now.isoformat(),
            )
            profile.status = "replied"
            profile.last_message_at = now
            profile.messages_json = list(messages)
            return "accepted"
        if delivery.outcome == "smtp_failure":
            code = int(delivery.smtp_code or 0)
            message.update(smtp_code=code, status_reason=delivery.reason)
            if 400 <= code < 500:
                message["delivery_status"] = "temporary_failed"
                profile.status = "need_reply" if claimed.message_kind == "followup_reply" else "draft"
                profile.messages_json = list(messages)
                return "temporary_failed"
            message["delivery_status"] = "hard_failed"
            profile.status = "closed"
            profile.messages_json = list(messages)
            return "hard_failed"
        message.update(
            delivery_status="delivery_unknown",
            status_reason=delivery.reason,
            smtp_code=None,
        )
        profile.status = "review"
        profile.messages_json = list(messages)
        return "delivery_unknown"


def _latest_inbound(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    inbound = [message for message in messages if message.get("direction") == "inbound"]
    return max(inbound, key=lambda message: str(message.get("occurred_at") or ""), default=None)


def _find_message(
    messages: list[dict[str, Any]], *, kind: str, delivery_statuses: set[str]
) -> dict[str, Any] | None:
    return next(
        (
            message for message in reversed(messages)
            if message.get("message_kind") == kind
            and message.get("delivery_status") in delivery_statuses
        ),
        None,
    )


def _reply_subject(subject: str | None) -> str:
    clean = (subject or "COOJOY Creator Partnership").strip()
    return clean if clean.lower().startswith("re:") else f"Re: {clean}"


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _string_list(value: object) -> list[str]:
    return [str(item).strip().lower() for item in value if str(item).strip()] if isinstance(value, list) else []


def _empty_delivery_counts() -> dict[str, int]:
    return {
        "accepted": 0,
        "temporary_failed": 0,
        "hard_failed": 0,
        "delivery_unknown": 0,
        "skipped": 0,
    }
