from __future__ import annotations

from dataclasses import dataclass
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from datetime import UTC, datetime
import re


FAILED_RECIPIENT_PATTERNS = [
    re.compile(r"(?:Final|Original)-Recipient:\s*rfc822;\s*([^\s;]+)", re.IGNORECASE),
    re.compile(r"(?:failed recipient|delivery to)\s*[<:]?\s*([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", re.IGNORECASE),
]
MESSAGE_ID_PATTERN = re.compile(r"<[^<>\s]+@[^<>\s]+>")
ORIGINAL_MESSAGE_ID_PATTERN = re.compile(
    r"^Original-Message-ID:\s*(<[^<>\s]+@[^<>\s]+>)",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class ParsedInbound:
    message_id: str | None
    related_email: str | None
    from_email: str | None
    message_type: str
    received_at: datetime
    subject: str | None
    body_text: str | None
    referenced_message_ids: tuple[str, ...]


def parse_inbound(message: Message, *, max_body_chars: int = 16_000) -> ParsedInbound:
    from_email = parseaddr(str(message.get("From") or ""))[1].strip().lower() or None
    subject = str(message.get("Subject") or "").strip() or None
    body = extract_text_body(message, max_chars=max_body_chars)
    # DSN details often live in message/delivery-status rather than text/plain,
    # so use the full serialized message for classification and recipient parsing.
    diagnostic_text = message.as_string()[:200_000]
    message_type = classify_message(
        message,
        from_email=from_email,
        subject=subject,
        body="\n".join(value for value in (body, diagnostic_text) if value),
    )
    related_email = (
        extract_failed_recipient(diagnostic_text)
        if message_type in {"hard_bounce", "soft_bounce"}
        else from_email
    )
    return ParsedInbound(
        message_id=extract_message_id(message),
        related_email=related_email,
        from_email=from_email,
        message_type=message_type,
        received_at=parse_received_at(str(message.get("Date") or "")),
        subject=subject,
        body_text=_body_for_storage(message_type, body, max_body_chars),
        referenced_message_ids=extract_referenced_message_ids(message, diagnostic_text),
    )


def extract_message_id(message: Message) -> str | None:
    values = MESSAGE_ID_PATTERN.findall(str(message.get("Message-ID") or ""))
    return values[0].lower() if values else None


def extract_referenced_message_ids(message: Message, diagnostic_text: str) -> tuple[str, ...]:
    """Return only IDs that can identify the original outbound thread."""
    values: list[str] = []
    for part in message.walk():
        for header_name in ("In-Reply-To", "References", "Original-Message-ID"):
            for raw_value in part.get_all(header_name, []):
                values.extend(MESSAGE_ID_PATTERN.findall(str(raw_value)))
        # Delivery reports can attach the original message as message/rfc822.
        # Its Message-ID is relevant; the top-level inbound Message-ID is not.
        if part is not message:
            for raw_value in part.get_all("Message-ID", []):
                values.extend(MESSAGE_ID_PATTERN.findall(str(raw_value)))
    values.extend(ORIGINAL_MESSAGE_ID_PATTERN.findall(diagnostic_text))
    return tuple(dict.fromkeys(value.lower() for value in values))


def classify_message(message: Message, *, from_email: str | None, subject: str | None, body: str | None) -> str:
    auto_submitted = str(message.get("Auto-Submitted") or "").strip().lower()
    subject_lower = (subject or "").lower()
    from_lower = (from_email or "").lower()
    body_lower = (body or "").lower()

    bounce_hint = (
        message.get_content_type() == "multipart/report"
        or "mailer-daemon" in from_lower
        or "postmaster" in from_lower
        or any(token in subject_lower for token in ("undeliverable", "delivery status notification", "mail delivery failed"))
    )
    if bounce_hint:
        if any(token in body_lower for token in ("4.1.", "4.2.", "4.3.", "temporary", "delayed")):
            return "soft_bounce"
        return "hard_bounce"
    if auto_submitted and auto_submitted != "no":
        return "auto_reply"
    if any(token in subject_lower for token in ("automatic reply", "auto reply", "out of office", "自动回复")):
        return "auto_reply"
    if any(token in body_lower for token in ("unsubscribe me", "remove me from", "停止发送", "退订")):
        return "unsubscribe"
    return "human_reply"


def extract_failed_recipient(body: str) -> str | None:
    for pattern in FAILED_RECIPIENT_PATTERNS:
        match = pattern.search(body)
        if match:
            return match.group(1).strip("<>.,; ").lower()
    return None


def extract_text_body(message: Message, *, max_chars: int) -> str | None:
    parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            if part.get_content_type() != "text/plain":
                continue
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                parts.append(payload.decode(charset, errors="replace"))
    elif message.get_content_type() == "text/plain":
        payload = message.get_payload(decode=True)
        if payload:
            charset = message.get_content_charset() or "utf-8"
            parts.append(payload.decode(charset, errors="replace"))
    text_value = "\n".join(parts).strip()
    return text_value[:max_chars] if text_value else None


def parse_received_at(raw_date: str) -> datetime:
    try:
        value = parsedate_to_datetime(raw_date)
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        return datetime.now(UTC)


def _body_for_storage(message_type: str, body: str | None, max_chars: int) -> str | None:
    if not body:
        return None
    if message_type in {"hard_bounce", "soft_bounce"}:
        return None
    if message_type == "auto_reply":
        return body[:1_000]
    return body[:max_chars]
