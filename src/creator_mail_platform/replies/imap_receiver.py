from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
import imaplib
import logging
import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError

from ..core.config import get_settings
from ..core.db import session_scope
from ..core.models import (
    ActivityContact,
    CampaignResponseProfile,
    MailboxCheckpoint,
)
from ..core.utils import normalize_email_key
from ..outreach.sender import SmtpAccount, load_imap_accounts
from .inbound_parser import ParsedInbound, parse_inbound


logger = logging.getLogger(__name__)

IMAP_FETCH_BATCH_SIZE = 100
COUNT_FIELDS = (
    "fetched",
    "fetch_skipped",
    "matched",
    "campaign_matched",
    "followup_matched",
    "unmatched",
    "duplicates",
    "checkpoint_initialized",
    "checkpoint_reset",
)


@dataclass(frozen=True)
class FetchedInbound:
    uid: int
    parsed: ParsedInbound | None


def receive_once(mailbox_email: str | None = None) -> dict[str, Any]:
    """Poll every configured mailbox once and persist new messages."""
    settings = get_settings()
    accounts = load_imap_accounts(settings)
    if not accounts:
        raise RuntimeError(
            "No IMAP accounts are enabled in SMTP_ACCOUNTS_FILE."
        )
    if mailbox_email:
        mailbox_key = normalize_email_key(mailbox_email)
        account = accounts.get(mailbox_key)
        if account is None:
            raise RuntimeError(f"IMAP mailbox is not enabled: {mailbox_key}")
        accounts = {mailbox_key: account}

    totals: dict[str, Any] = {field: 0 for field in COUNT_FIELDS}
    totals.update(
        {
            "mailboxes_configured": len(accounts),
            "mailbox_errors": 0,
            "mailboxes": [],
        }
    )

    for account in accounts.values():
        try:
            result = _receive_mailbox(
                account,
                max_body_chars=settings.max_inbound_body_chars,
            )
        except Exception as exc:  # Keep the other mailboxes running.
            logger.exception("IMAP poll failed for %s", account.from_email)
            totals["mailbox_errors"] += 1
            totals["mailboxes"].append(
                {
                    "mailbox": account.from_email,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        for field in COUNT_FIELDS:
            totals[field] += result[field]
        totals["mailboxes"].append(
            {"mailbox": account.from_email, **result}
        )

    return totals


def _receive_mailbox(
    account: SmtpAccount,
    *,
    max_body_chars: int,
) -> dict[str, int]:
    counts = {field: 0 for field in COUNT_FIELDS}
    connection = _open_imap(account)

    try:
        status, _ = connection.select(account.imap_folder, readonly=True)
        if status != "OK":
            raise RuntimeError(
                f"Unable to select IMAP folder {account.imap_folder!r}."
            )

        uidvalidity = _uidvalidity(connection)
        checkpoint = _get_checkpoint(account.from_email)

        if checkpoint is None:
            current_max_uid = _current_max_uid(connection)
            counts["checkpoint_initialized"] = int(
                _initialize_checkpoint(
                    account.from_email,
                    uidvalidity,
                    current_max_uid,
                )
            )
            return counts

        if checkpoint.uidvalidity != uidvalidity:
            current_max_uid = _current_max_uid(connection)
            counts["checkpoint_reset"] = int(
                _reset_checkpoint(
                    account.from_email,
                    uidvalidity,
                    current_max_uid,
                )
            )
            return counts

        messages = _fetch_new_messages(
            connection,
            after_uid=checkpoint.last_uid,
            max_body_chars=max_body_chars,
        )
        counts["fetched"] = len(messages)
        if not messages:
            return counts

        applied = _apply_fetched_batch(
            account.from_email,
            uidvalidity,
            messages,
        )
        counts.update(applied)
        return counts
    finally:
        try:
            connection.logout()
        except (OSError, imaplib.IMAP4.error):
            logger.debug(
                "IMAP logout failed for %s",
                account.from_email,
                exc_info=True,
            )


def _open_imap(account: SmtpAccount) -> imaplib.IMAP4:
    if account.imap_use_ssl:
        connection: imaplib.IMAP4 = imaplib.IMAP4_SSL(
            account.imap_host,
            account.imap_port,
            timeout=30,
        )
    else:
        connection = imaplib.IMAP4(
            account.imap_host,
            account.imap_port,
            timeout=30,
        )
    connection.login(account.imap_username, account.imap_password)
    return connection


def _uidvalidity(connection: imaplib.IMAP4) -> str:
    status, values = connection.response("UIDVALIDITY")
    if status != "UIDVALIDITY" or not values or values[0] is None:
        raise RuntimeError("IMAP server did not provide UIDVALIDITY.")
    raw = values[0]
    return raw.decode("ascii") if isinstance(raw, bytes) else str(raw)


def _current_max_uid(connection: imaplib.IMAP4) -> int:
    status, values = connection.uid("search", None, "ALL")
    if status != "OK":
        raise RuntimeError("Unable to list IMAP message UIDs.")
    uids = _parse_uid_values(values)
    return max(uids, default=0)


def _fetch_new_messages(
    connection: imaplib.IMAP4,
    *,
    after_uid: int,
    max_body_chars: int,
) -> list[FetchedInbound]:
    status, values = connection.uid(
        "search",
        None,
        f"UID {after_uid + 1}:*",
    )
    if status != "OK":
        raise RuntimeError("Unable to search for new IMAP messages.")

    uids = sorted(
        uid for uid in set(_parse_uid_values(values)) if uid > after_uid
    )[:IMAP_FETCH_BATCH_SIZE]

    messages: list[FetchedInbound] = []
    for uid in uids:
        status, fetched = connection.uid("fetch", str(uid), "(RFC822)")
        raw_message = _extract_raw_message(fetched)
        if status != "OK" or raw_message is None:
            logger.warning("Skipping unavailable IMAP message UID %s", uid)
            messages.append(FetchedInbound(uid=uid, parsed=None))
            continue

        try:
            message = BytesParser(policy=policy.default).parsebytes(raw_message)
            parsed = parse_inbound(
                message,
                max_body_chars=max_body_chars,
            )
        except Exception:
            logger.warning("Skipping malformed IMAP message UID %s", uid, exc_info=True)
            parsed = None
        messages.append(FetchedInbound(uid=uid, parsed=parsed))
    return messages


def _parse_uid_values(values: list[Any] | tuple[Any, ...] | None) -> list[int]:
    if not values:
        return []
    uids: list[int] = []
    for value in values:
        if isinstance(value, bytes):
            parts = value.split()
        elif isinstance(value, str):
            parts = value.split()
        else:
            continue
        for part in parts:
            try:
                uids.append(int(part))
            except (TypeError, ValueError):
                continue
    return uids


def _extract_raw_message(fetched: Any) -> bytes | None:
    if not fetched:
        return None
    for item in fetched:
        if (
            isinstance(item, tuple)
            and len(item) >= 2
            and isinstance(item[1], bytes)
        ):
            return item[1]
    return None


def _get_checkpoint(mailbox_email: str) -> MailboxCheckpoint | None:
    with session_scope() as session:
        return session.get(MailboxCheckpoint, normalize_email_key(mailbox_email))


def _initialize_checkpoint(
    mailbox_email: str,
    uidvalidity: str,
    current_max_uid: int,
) -> bool:
    mailbox_key = normalize_email_key(mailbox_email)
    with session_scope() as session:
        checkpoint = session.get(
            MailboxCheckpoint,
            mailbox_key,
            with_for_update=True,
        )
        if checkpoint is not None:
            return False
        session.add(
            MailboxCheckpoint(
                mailbox_email=mailbox_key,
                uidvalidity=uidvalidity,
                last_uid=current_max_uid,
            )
        )
        return True


def _reset_checkpoint(
    mailbox_email: str,
    uidvalidity: str,
    current_max_uid: int,
) -> bool:
    mailbox_key = normalize_email_key(mailbox_email)
    with session_scope() as session:
        checkpoint = session.get(
            MailboxCheckpoint,
            mailbox_key,
            with_for_update=True,
        )
        if checkpoint is None:
            session.add(
                MailboxCheckpoint(
                    mailbox_email=mailbox_key,
                    uidvalidity=uidvalidity,
                    last_uid=current_max_uid,
                )
            )
            return True
        if checkpoint.uidvalidity == uidvalidity:
            return False
        checkpoint.uidvalidity = uidvalidity
        checkpoint.last_uid = current_max_uid
        return True


def _apply_fetched_batch(
    mailbox_email: str,
    uidvalidity: str,
    messages: list[FetchedInbound],
) -> dict[str, int]:
    """Apply a fetched mailbox batch and its checkpoint atomically."""
    counts = {
        "matched": 0,
        "fetch_skipped": 0,
        "campaign_matched": 0,
        "followup_matched": 0,
        "unmatched": 0,
        "duplicates": 0,
    }
    mailbox_key = normalize_email_key(mailbox_email)

    with session_scope() as session:
        checkpoint = session.get(
            MailboxCheckpoint,
            mailbox_key,
            with_for_update=True,
        )
        if checkpoint is None:
            raise RuntimeError(
                f"Missing IMAP checkpoint for mailbox {mailbox_key}."
            )
        if checkpoint.uidvalidity != uidvalidity:
            raise RuntimeError(
                f"UIDVALIDITY changed while polling mailbox {mailbox_key}."
            )

        current_uid = checkpoint.last_uid
        for item in sorted(messages, key=lambda entry: entry.uid):
            if item.uid <= current_uid:
                counts["duplicates"] += 1
                continue

            if item.parsed is None:
                counts["fetch_skipped"] += 1
                checkpoint.last_uid = item.uid
                current_uid = item.uid
                continue

            followup_result = _apply_to_followup(
                session,
                item.parsed,
                mailbox_email=mailbox_key,
                uidvalidity=uidvalidity,
                uid=item.uid,
            )
            if followup_result == "duplicate":
                counts["duplicates"] += 1
            elif followup_result == "matched":
                counts["matched"] += 1
                counts["followup_matched"] += 1
            elif _apply_to_contact(
                session,
                item.parsed,
                mailbox_email=mailbox_key,
            ):
                counts["matched"] += 1
                counts["campaign_matched"] += 1
            else:
                counts["unmatched"] += 1

            checkpoint.last_uid = item.uid
            current_uid = item.uid

    return counts


def _apply_to_followup(
    session: Any,
    parsed: ParsedInbound,
    *,
    mailbox_email: str,
    uidvalidity: str,
    uid: int,
) -> str | None:
    """Append a reply to the creator's compact response-profile timeline."""
    followup_mailbox = normalize_email_key(get_settings().followup_mailbox_email)
    if normalize_email_key(mailbox_email) != followup_mailbox:
        return None
    related_email = normalize_email_key(parsed.related_email or "")
    referenced_ids = tuple(
        dict.fromkeys(
            message_id.strip().lower()
            for message_id in parsed.referenced_message_ids
            if message_id and message_id.strip()
        )
    )
    if not related_email or not referenced_ids:
        return None

    try:
        with session.begin_nested():
            candidates = list(
                session.scalars(
                    select(CampaignResponseProfile).where(
                        CampaignResponseProfile.mailbox_email == mailbox_email,
                        func.lower(CampaignResponseProfile.recipient_email) == related_email,
                    ).with_for_update()
                )
            )
    except ProgrammingError:
        # Primary campaign replies must still work before this optional table
        # is prepared for the first time.
        return None
    profile = next(
        (
            candidate
            for candidate in candidates
            if any(
                message.get("direction") == "outbound"
                and message.get("message_kind")
                in {"rejection_notice", "followup_reply"}
                and str(message.get("message_id") or "").lower() in referenced_ids
                for message in (candidate.messages_json or [])
            )
        ),
        None,
    )
    if profile is None:
        return None

    if parsed.message_type not in {"hard_bounce", "soft_bounce"}:
        if normalize_email_key(parsed.from_email or "") != related_email:
            return None

    source_key = f"imap:{mailbox_email}:{uidvalidity}:{uid}"
    messages = list(profile.messages_json or [])
    inbound_message_id = parsed.message_id.lower() if parsed.message_id else None
    if any(
        message.get("source_key") == source_key
        or (
            inbound_message_id
            and str(message.get("message_id") or "").lower() == inbound_message_id
        )
        for message in messages
    ):
        return "duplicate"

    now = datetime.now(UTC)
    outbound_id = next(
        (
            str(message.get("message_id")).lower()
            for message in reversed(messages)
            if message.get("direction") == "outbound"
            and message.get("message_kind")
            in {"rejection_notice", "followup_reply"}
            and str(message.get("message_id") or "").lower() in referenced_ids
        ),
        None,
    )
    messages.append(
        {
            "source_key": source_key,
            "direction": "inbound",
            "message_kind": _followup_message_kind(parsed.message_type),
            "message_id": inbound_message_id,
            "in_reply_to": outbound_id,
            "references": list(referenced_ids),
            "from_email": parsed.from_email,
            "to_email": mailbox_email,
            "subject": parsed.subject,
            "body_text": parsed.body_text,
            "occurred_at": parsed.received_at.isoformat(),
            "delivery_status": "received",
            "status_reason": None,
            "semantic_intent": parsed.message_type,
            "metadata": {},
        }
    )
    profile.messages_json = messages
    profile.last_message_at = parsed.received_at
    profile.updated_at = now
    contact = session.get(ActivityContact, profile.creator_id, with_for_update=True)
    if parsed.message_type == "human_reply":
        profile.status = "need_reply"
    elif parsed.message_type == "unsubscribe":
        profile.status = "closed"
        if contact is not None:
            contact.email_status = "suppressed"
            contact.status_reason = "unsubscribed"
    elif parsed.message_type == "hard_bounce":
        profile.status = "closed"
        if contact is not None:
            contact.email_status = "invalid"
            contact.status_reason = "hard_bounce"
    else:
        if profile.status != "need_reply":
            profile.status = "replied"
        if parsed.message_type == "soft_bounce" and contact is not None:
            contact.status_reason = "soft_bounce"
    return "matched"


def _followup_message_kind(message_type: str) -> str:
    return {
        "human_reply": "creator_reply",
        "auto_reply": "auto_reply",
        "unsubscribe": "unsubscribe",
        "hard_bounce": "hard_bounce",
        "soft_bounce": "soft_bounce",
    }.get(message_type, "inbound_message")


def _apply_to_contact(
    session: Any,
    parsed: ParsedInbound,
    *,
    mailbox_email: str | None = None,
) -> bool:
    contact = _find_contact(
        session,
        parsed,
        mailbox_email=mailbox_email,
    )
    if contact is None:
        return False
    _update_contact(contact, parsed)
    return True


def _find_contact(
    session: Any,
    parsed: ParsedInbound,
    *,
    mailbox_email: str | None = None,
) -> ActivityContact | None:
    related_email = normalize_email_key(parsed.related_email)
    referenced_ids = tuple(
        dict.fromkeys(
            message_id.strip()
            for message_id in parsed.referenced_message_ids
            if message_id and message_id.strip()
        )
    )
    if not related_email or not referenced_ids:
        return None

    conditions = [
        ActivityContact.email == related_email,
        ActivityContact.outbound_message_id.in_(referenced_ids),
        ActivityContact.sent_at.is_not(None),
        ActivityContact.sent_at <= parsed.received_at,
    ]
    if mailbox_email:
        conditions.append(
            ActivityContact.sender_email == normalize_email_key(mailbox_email)
        )

    contact = session.execute(
        select(ActivityContact)
        .where(*conditions)
        .order_by(ActivityContact.sent_at.desc(), ActivityContact.creator_id)
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()
    if contact is None:
        return None

    if contact.outbound_message_id not in referenced_ids:
        return None
    if parsed.message_type not in {"hard_bounce", "soft_bounce"}:
        if normalize_email_key(parsed.from_email) != normalize_email_key(contact.email):
            return None

    return contact


def _update_contact(contact: ActivityContact, parsed: ParsedInbound) -> None:
    contact.reply_type = parsed.message_type
    contact.reply_subject = parsed.subject
    contact.reply_body_text = parsed.body_text
    contact.last_reply_at = parsed.received_at
    contact.reply_count = (contact.reply_count or 0) + 1

    if parsed.message_type == "hard_bounce":
        contact.email_status = "invalid"
        contact.status_reason = "hard_bounce"
    elif parsed.message_type == "soft_bounce":
        contact.status_reason = "soft_bounce"
    elif parsed.message_type in {"human_reply", "auto_reply"}:
        contact.email_status = "usable"
        contact.status_reason = None
    elif parsed.message_type == "unsubscribe":
        contact.email_status = "suppressed"
        contact.status_reason = "unsubscribed"

def listen_forever(mailbox_email: str | None = None) -> None:
    settings = get_settings()
    logger.info("IMAP listener started")
    while True:
        try:
            result = receive_once(mailbox_email=mailbox_email)
            if result["mailbox_errors"]:
                logger.warning("IMAP poll completed with errors: %s", result)
            else:
                logger.info("IMAP poll completed: %s", result)
        except Exception:
            logger.exception("IMAP receive cycle failed")
        time.sleep(settings.imap_poll_seconds)
