from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from html import escape
import json
from pathlib import Path
import re
import smtplib
import time
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import PROJECT_ROOT, Settings, get_settings
from ..core.db import get_session_factory, session_scope
from ..core.models import ActivityContact
from ..core.utils import extract_send_address, normalize_email_key


ELIGIBLE_SEND_STATUSES = ("pending", "temporary_failed")
BLOCKED_EMAIL_STATUSES = ("invalid", "suppressed")
EMAIL_LOGO_PATH = PROJECT_ROOT / "frontend" / "creator_pass" / "assets" / "coojoy-logo.jpg"
URL_PATTERN = re.compile(r"https://[^\s<>]+")


@dataclass(frozen=True)
class SmtpAccount:
    from_email: str
    host: str = ""
    port: int = 465
    username: str = ""
    password: str = field(default="", repr=False)
    use_ssl: bool = True
    use_starttls: bool = False
    rate_per_second: float = 1.0
    max_parallel_sends: int = 1
    messages_per_connection: int = 50
    imap_enabled: bool = False
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = field(default="", repr=False)
    imap_use_ssl: bool = True
    imap_folder: str = "INBOX"


@dataclass(frozen=True)
class PreparedMessage:
    subject: str
    body_text: str

    def assert_ready_for_real_send(self) -> None:
        if "DRAFT_ONLY" in self.subject or "DRAFT_ONLY" in self.body_text:
            raise RuntimeError(
                "The prepared message is still marked DRAFT_ONLY. "
                "Approve the generated content first."
            )


@dataclass(frozen=True)
class ClaimedMessage:
    creator_id: str
    recipient: str
    message_id: str
    subject: str
    body_text: str


@dataclass(frozen=True)
class DeliveryResult:
    outcome: str
    smtp_code: int | None = None
    reason: str | None = None
    connection_broken: bool = False


def load_smtp_accounts(settings: Settings | None = None) -> dict[str, SmtpAccount]:
    """Load enabled SMTP credentials from the ignored local accounts file."""
    settings = settings or get_settings()
    path = _resolve_accounts_path(settings.smtp_accounts_file)
    if not path.exists():
        raise RuntimeError(f"SMTP accounts file does not exist: {path}")

    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    raw_accounts = payload.get("accounts") if isinstance(payload, dict) else payload
    if not isinstance(raw_accounts, list):
        raise RuntimeError("SMTP accounts file must contain an accounts list")

    accounts: dict[str, SmtpAccount] = {}
    for index, raw in enumerate(raw_accounts, start=1):
        if not isinstance(raw, dict):
            raise RuntimeError(f"SMTP account #{index} must be an object")
        if not _as_bool(raw.get("enabled", True), "enabled"):
            continue
        account = _parse_account(raw, index=index)
        if account.from_email in accounts:
            raise RuntimeError(f"Duplicate SMTP account: {account.from_email}")
        accounts[account.from_email] = account
    return accounts


def get_smtp_account(
    sender_email: str,
    *,
    settings: Settings | None = None,
    require_credentials: bool = False,
) -> SmtpAccount:
    normalized = normalize_email_key(sender_email)
    if extract_send_address(normalized) is None:
        raise ValueError(f"Invalid sender email: {sender_email}")
    account = load_smtp_accounts(settings).get(normalized)
    if account is None:
        if require_credentials:
            raise RuntimeError(
                f"No local SMTP configuration found for {normalized}. "
                "Add it to SMTP_ACCOUNTS_FILE first."
            )
        return SmtpAccount(from_email=normalized)
    if require_credentials:
        validate_smtp_account(account)
    return account


def validate_smtp_account(account: SmtpAccount) -> None:
    missing = [
        name
        for name, value in {
            "host": account.host,
            "username": account.username,
            "password": account.password,
            "from_email": account.from_email,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"SMTP account {account.from_email} is missing: {', '.join(missing)}"
        )


def load_imap_accounts(settings: Settings | None = None) -> dict[str, SmtpAccount]:
    """Return enabled mail accounts that are explicitly opted into IMAP."""
    accounts = {
        email: account
        for email, account in load_smtp_accounts(settings).items()
        if account.imap_enabled
    }
    for account in accounts.values():
        validate_imap_account(account)
    return accounts


def validate_imap_account(account: SmtpAccount) -> None:
    missing = [
        name
        for name, value in {
            "imap_host": account.imap_host,
            "imap_username": account.imap_username,
            "imap_password": account.imap_password,
            "from_email": account.from_email,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"IMAP account {account.from_email} is missing: {', '.join(missing)}"
        )


def send_batch(
    *,
    batch_no: int,
    sender_email: str | None = None,
    limit: int | None = None,
    execute: bool = False,
) -> dict[str, int]:
    """Inspect or send one explicit batch using the shared accounts file."""
    _validate_execution_guard(execute=execute)
    if execute and not sender_email:
        raise ValueError("--sender-email is required when send-batch uses --execute")
    sender = normalize_email_key(sender_email) if sender_email else None
    if sender and extract_send_address(sender) is None:
        raise ValueError(f"Invalid sender email: {sender_email}")
    account = (
        get_smtp_account(sender, require_credentials=True)
        if sender and execute
        else SmtpAccount(from_email="")
    )

    with session_scope() as session:
        statement = (
            select(ActivityContact.creator_id)
            .where(
                ActivityContact.batch_no == batch_no,
                ActivityContact.send_status.in_(ELIGIBLE_SEND_STATUSES),
                ActivityContact.email_status.not_in(BLOCKED_EMAIL_STATUSES),
            )
            .order_by(ActivityContact.creator_id)
        )
        if sender:
            # 正式发送只允许当前账号处理已经分配给自己的联系人。
            statement = statement.where(func.lower(ActivityContact.sender_email) == sender)
        if limit is not None:
            statement = statement.limit(limit)
        creator_ids = list(session.scalars(statement))
    return _send_emails(
        creator_ids,
        account=account,
        execute=execute,
        expected_sender=sender if execute else None,
    )


def _send_emails(
    creator_ids: list[str],
    *,
    account: SmtpAccount,
    execute: bool,
    expected_sender: str | None,
) -> dict[str, int]:
    counts = _empty_counts()
    counts["selected"] = len(creator_ids)
    if not creator_ids:
        return counts
    if not execute:
        with session_scope() as session:
            contacts = {
                contact.creator_id: contact
                for contact in session.scalars(
                    select(ActivityContact).where(ActivityContact.creator_id.in_(creator_ids))
                )
            }
            for creator_id in creator_ids:
                contact = contacts.get(creator_id)
                if contact is not None:
                    _prepared_message(contact).assert_ready_for_real_send()
        counts["dry_run"] = len(creator_ids)
        return counts

    worker_count = min(account.max_parallel_sends, len(creator_ids))
    if worker_count == 1:
        return _send_worker(
            creator_ids,
            account=account,
            expected_sender=expected_sender,
        )

    chunks = [creator_ids[index::worker_count] for index in range(worker_count)]
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix=f"smtp-{account.from_email.split('@', 1)[0]}",
    ) as executor:
        results = list(
            executor.map(
                lambda chunk: _send_worker(
                    chunk,
                    account=account,
                    expected_sender=expected_sender,
                ),
                chunks,
            )
        )

    counts = _empty_counts()
    for result in results:
        for name in counts:
            counts[name] += result[name]
    return counts


def _send_worker(
    creator_ids: list[str],
    *,
    account: SmtpAccount,
    expected_sender: str | None,
) -> dict[str, int]:
    """Send one disjoint slice using its own SMTP connection and DB session."""
    counts = _empty_counts()
    counts["selected"] = len(creator_ids)
    connection: smtplib.SMTP | None = None
    messages_on_connection = 0
    session = get_session_factory()()
    try:
        for creator_id in creator_ids:
            if connection is None:
                connection = _open_smtp(account)
                messages_on_connection = 0

            claimed, preliminary_outcome = _claim_message(
                session,
                creator_id,
                account=account,
                expected_sender=expected_sender,
            )
            session.expunge_all()
            if claimed is None:
                if preliminary_outcome is not None:
                    counts[preliminary_outcome] += 1
                continue

            delivery = _deliver_message(
                connection,
                _build_message(claimed, account=account),
            )
            outcome = _record_delivery(session, claimed, delivery)
            session.expunge_all()
            counts[outcome] += 1
            messages_on_connection += 1
            time.sleep(1.0 / account.rate_per_second)

            if delivery.connection_broken:
                _close_smtp(connection)
                connection = None
                # Stop this slice immediately. The resumable queue will wait
                # and restart it with a fresh connection instead of producing
                # a chain of ambiguous SMTP outcomes on an unstable network.
                break
            if messages_on_connection >= account.messages_per_connection:
                _close_smtp(connection)
                connection = None
    finally:
        session.close()
        if connection is not None:
            _close_smtp(connection)
    return counts


def _claim_message(
    session: Session,
    creator_id: str,
    *,
    account: SmtpAccount,
    expected_sender: str | None,
) -> tuple[ClaimedMessage | None, str | None]:
    """Commit the sending claim before SMTP so a crash cannot cause auto-resend."""
    with session.begin():
        contact = session.get(ActivityContact, creator_id, with_for_update=True)
        if contact is None or contact.send_status not in ELIGIBLE_SEND_STATUSES:
            return None, None
        if contact.email_status in BLOCKED_EMAIL_STATUSES:
            return None, None
        if expected_sender and normalize_email_key(contact.sender_email or "") != expected_sender:
            return None, None

        address = extract_send_address(contact.email)
        if address is None:
            contact.send_status = "hard_failed"
            contact.email_status = "invalid"
            contact.status_reason = "invalid_format"
            return None, "invalid"

        template = _prepared_message(contact)
        template.assert_ready_for_real_send()
        message_id = contact.outbound_message_id or _new_outbound_message_id(
            account.from_email
        )
        contact.sent_subject = template.subject
        contact.sent_body_text = template.body_text
        contact.outbound_message_id = message_id
        contact.send_status = "sending"
        contact.status_reason = "smtp_in_progress"
        contact.smtp_code = None
        claimed = ClaimedMessage(
            creator_id=contact.creator_id,
            recipient=address,
            message_id=message_id,
            subject=template.subject,
            body_text=template.body_text,
        )
    return claimed, None


def _build_message(claimed: ClaimedMessage, *, account: SmtpAccount) -> EmailMessage:
    message = EmailMessage()
    message["From"] = account.from_email
    message["To"] = claimed.recipient
    message["Subject"] = claimed.subject
    message["Date"] = format_datetime(datetime.now(UTC))
    message["Message-ID"] = claimed.message_id
    _set_message_content(message, claimed.body_text)
    return message


def _deliver_message(
    connection: smtplib.SMTP,
    message: EmailMessage,
) -> DeliveryResult:
    try:
        refused = connection.send_message(message)
        if refused:
            code, reason = next(iter(refused.values()))
            return DeliveryResult(
                outcome="smtp_failure",
                smtp_code=int(code),
                reason=_decode_reason(reason),
            )
        return DeliveryResult(outcome="accepted", smtp_code=250)
    except smtplib.SMTPRecipientsRefused as exc:
        code, reason = next(iter(exc.recipients.values()))
        return DeliveryResult(
            outcome="smtp_failure",
            smtp_code=int(code),
            reason=_decode_reason(reason),
        )
    except smtplib.SMTPResponseException as exc:
        code = int(exc.smtp_code)
        return DeliveryResult(
            outcome="smtp_failure",
            smtp_code=code,
            reason=_decode_reason(exc.smtp_error),
            connection_broken=code == 421,
        )
    except (OSError, smtplib.SMTPException) as exc:
        # The server may have accepted the message before the socket broke.
        # Keep it out of automatic retries until a human reconciles it.
        return DeliveryResult(
            outcome="delivery_unknown",
            reason=f"{type(exc).__name__}: {exc}"[:500],
            connection_broken=True,
        )


def _record_delivery(
    session: Session,
    claimed: ClaimedMessage,
    delivery: DeliveryResult,
) -> str:
    with session.begin():
        contact = session.get(ActivityContact, claimed.creator_id, with_for_update=True)
        if contact is None or contact.outbound_message_id != claimed.message_id:
            raise RuntimeError(
                f"Cannot finalize SMTP result for {claimed.creator_id}: claim changed"
            )
        if delivery.outcome == "accepted":
            contact.send_status = "smtp_accepted"
            contact.email_status = "possibly_usable"
            contact.status_reason = None
            contact.smtp_code = 250
            contact.sent_at = datetime.now(UTC)
            return "accepted"
        if delivery.outcome == "smtp_failure":
            assert delivery.smtp_code is not None
            _apply_smtp_failure(
                contact,
                delivery.smtp_code,
                delivery.reason or "",
            )
            return contact.send_status

        contact.send_status = "sending"
        contact.status_reason = "smtp_result_unknown"
        contact.smtp_code = None
        contact.retry_count += 1
        return "delivery_unknown"


def _empty_counts() -> dict[str, int]:
    return {
        "selected": 0,
        "dry_run": 0,
        "accepted": 0,
        "temporary_failed": 0,
        "hard_failed": 0,
        "invalid": 0,
        "delivery_unknown": 0,
    }


def _prepared_message(contact: ActivityContact) -> PreparedMessage:
    if not contact.sent_subject or not contact.sent_body_text:
        raise RuntimeError(
            f"Creator {contact.creator_id} has no approved sent_subject/sent_body_text"
        )
    return PreparedMessage(subject=contact.sent_subject, body_text=contact.sent_body_text)


def _set_message_content(message: EmailMessage, body_text: str) -> None:
    """Keep auditable plain text in the database; build HTML only for delivery."""
    message.set_content(body_text)

    logo_bytes: bytes | None = None
    try:
        logo_bytes = EMAIL_LOGO_PATH.read_bytes()
    except OSError:
        # A missing optional asset must not block an otherwise valid campaign.
        logo_bytes = None

    message.add_alternative(
        _render_html_body(body_text, include_logo=logo_bytes is not None),
        subtype="html",
    )
    if logo_bytes is not None:
        html_part = message.get_payload()[-1]
        html_part.add_related(
            logo_bytes,
            maintype="image",
            subtype="jpeg",
            cid="<coojoy-logo>",
            filename="coojoy-logo.jpg",
            disposition="inline",
        )


def _render_html_body(body_text: str, *, include_logo: bool) -> str:
    paragraphs = []
    for paragraph in body_text.split("\n\n"):
        lines = "<br>".join(_linkify_line(line) for line in paragraph.splitlines())
        paragraphs.append(
            f'<p style="margin:0 0 16px;line-height:1.65">{lines}</p>'
        )

    logo = ""
    if include_logo:
        logo = (
            '<div style="margin-top:24px">'
            '<img src="cid:coojoy-logo" alt="COOJOY" width="144" '
            'style="display:block;width:144px;max-width:100%;height:auto;border:0">'
            "</div>"
        )

    return (
        '<!doctype html><html><body style="margin:0;padding:0;background:#ffffff">'
        '<div style="max-width:640px;margin:0 auto;padding:24px;'
        'font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#202124">'
        f"{''.join(paragraphs)}{logo}"
        "</div></body></html>"
    )


def _linkify_line(line: str) -> str:
    pieces: list[str] = []
    cursor = 0
    for match in URL_PATTERN.finditer(line):
        pieces.append(escape(line[cursor:match.start()]))
        url = match.group(0)
        pieces.append(
            f'<a href="{escape(url, quote=True)}" '
            f'style="color:#6546d7">{escape(url)}</a>'
        )
        cursor = match.end()
    pieces.append(escape(line[cursor:]))
    return "".join(pieces)


def _validate_execution_guard(*, execute: bool) -> None:
    if execute and not get_settings().smtp_execution_enabled:
        raise RuntimeError(
            "Real sending is disabled. Set SMTP_EXECUTION_ENABLED=true only after a controlled test is approved."
        )


def _open_smtp(account: SmtpAccount) -> smtplib.SMTP:
    if account.use_ssl:
        connection: smtplib.SMTP = smtplib.SMTP_SSL(account.host, account.port, timeout=30)
    else:
        connection = smtplib.SMTP(account.host, account.port, timeout=30)
    connection.ehlo()
    if account.use_starttls:
        connection.starttls()
        connection.ehlo()
    connection.login(account.username, account.password)
    return connection


def _close_smtp(connection: smtplib.SMTP) -> None:
    try:
        connection.quit()
    except (OSError, smtplib.SMTPException):
        connection.close()


def _apply_smtp_failure(contact: ActivityContact, code: int, reason: str) -> None:
    contact.smtp_code = code
    contact.retry_count += 1
    if 500 <= code <= 599:
        contact.send_status = "hard_failed"
        contact.email_status = "invalid"
        contact.status_reason = _classify_smtp_reason(reason)
    else:
        contact.send_status = "temporary_failed"
        contact.status_reason = "smtp_temporary_error"


def _classify_smtp_reason(reason: str) -> str:
    lowered = reason.lower()
    if any(
        token in lowered
        for token in ("user unknown", "mailbox not found", "does not exist", "5.1.1")
    ):
        return "mailbox_not_found"
    return "smtp_rejected"


def _decode_reason(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[:500]
    return str(value)[:500]


def _new_outbound_message_id(sender_email: str) -> str:
    _, separator, domain = sender_email.rpartition("@")
    safe_domain = domain.lower() if separator and domain else "coojoy.cn"
    return f"<creator-mail.{uuid4().hex}@{safe_domain}>"


def _resolve_accounts_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _parse_account(raw: dict[str, object], *, index: int) -> SmtpAccount:
    from_email = normalize_email_key(str(raw.get("from_email") or ""))
    if extract_send_address(from_email) is None:
        raise RuntimeError(f"SMTP account #{index} has an invalid from_email")
    rate = float(raw.get("rate_per_second", 1.0))
    if rate <= 0:
        raise RuntimeError(f"SMTP account {from_email} rate_per_second must be positive")
    max_parallel_sends = int(raw.get("max_parallel_sends", 1))
    if not 1 <= max_parallel_sends <= 8:
        raise RuntimeError(
            f"SMTP account {from_email} max_parallel_sends must be between 1 and 8"
        )
    messages_per_connection = int(raw.get("messages_per_connection", 50))
    if not 1 <= messages_per_connection <= 500:
        raise RuntimeError(
            f"SMTP account {from_email} messages_per_connection must be between 1 and 500"
        )
    smtp_username = str(raw.get("username") or "")
    smtp_password = str(raw.get("password") or "")
    return SmtpAccount(
        from_email=from_email,
        host=str(raw.get("host") or ""),
        port=int(raw.get("port", 465)),
        username=smtp_username,
        password=smtp_password,
        use_ssl=_as_bool(raw.get("use_ssl", True), "use_ssl"),
        use_starttls=_as_bool(raw.get("use_starttls", False), "use_starttls"),
        rate_per_second=rate,
        max_parallel_sends=max_parallel_sends,
        messages_per_connection=messages_per_connection,
        imap_enabled=_as_bool(raw.get("imap_enabled", False), "imap_enabled"),
        imap_host=str(raw.get("imap_host") or ""),
        imap_port=int(raw.get("imap_port", 993)),
        imap_username=str(raw.get("imap_username") or smtp_username),
        imap_password=str(raw.get("imap_password") or smtp_password),
        imap_use_ssl=_as_bool(raw.get("imap_use_ssl", True), "imap_use_ssl"),
        imap_folder=str(raw.get("imap_folder") or "INBOX"),
    )


def _as_bool(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise RuntimeError(f"SMTP account field {name} must be true or false")
