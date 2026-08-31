from __future__ import annotations

from email.utils import parseaddr
from urllib.parse import unquote

from email_validator import EmailNotValidError, validate_email


def clean_email_token(raw_email: str) -> str:
    """Remove common wrappers without guessing or splitting multiple addresses."""
    cleaned = unquote(raw_email or "").replace("\u200b", "").replace("\ufeff", "").strip()
    if cleaned.casefold().startswith("mailto:"):
        cleaned = cleaned[7:].split("?", 1)[0]
    if "<" in cleaned and ">" in cleaned:
        start = cleaned.rfind("<") + 1
        end = cleaned.find(">", start)
        if end > start and "@" in cleaned[start:end]:
            cleaned = cleaned[start:end]
    else:
        _, parsed = parseaddr(cleaned)
        if parsed:
            cleaned = parsed
    return cleaned.strip().strip("\"'<>[](){}").rstrip(".,;:").strip()


def extract_send_address(raw_email: str) -> str | None:
    """Return a normalized syntax-checked address without network access."""
    try:
        return validate_email(
            clean_email_token(raw_email),
            check_deliverability=False,
        ).normalized
    except EmailNotValidError:
        return None


def normalize_email_key(raw_email: str) -> str:
    """Return the stable lowercase key stored in activity_contacts."""
    address = extract_send_address(raw_email)
    return (address or clean_email_token(raw_email)).casefold()
