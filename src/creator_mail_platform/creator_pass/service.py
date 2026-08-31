from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
from typing import Any
from urllib.parse import urlencode

from ..core.models import ActivityContact


FORM_VERSION = "creator_pass_v1"
WELCOME_BONUS_USD = 5
CATEGORY_BONUS_USD = 5
CONTACT_BONUS_USD = 20
TERMINAL_REWARD_STATUSES = {"earned", "paid", "ineligible"}


class FormTokenError(ValueError):
    pass


def create_form_token(
    creator_id: str,
    *,
    secret: str,
    ttl_days: int = 90,
    now: datetime | None = None,
) -> str:
    _validate_secret(secret)
    if not creator_id or len(creator_id) > 256:
        raise ValueError("creator_id must contain 1-256 characters")
    issued_at = _as_utc(now or datetime.now(UTC))
    payload = {
        "exp": int((issued_at + timedelta(days=ttl_days)).timestamp()),
        "sub": creator_id,
        "v": 1,
    }
    encoded = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _sign(encoded, secret)
    return f"{encoded}.{signature}"


def verify_form_token(
    token: str,
    *,
    secret: str,
    now: datetime | None = None,
) -> str:
    _validate_secret(secret)
    try:
        encoded, supplied_signature = token.split(".", 1)
    except ValueError as exc:
        raise FormTokenError("invalid token") from exc
    expected_signature = _sign(encoded, secret)
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise FormTokenError("invalid token")
    try:
        payload = json.loads(_b64decode(encoded))
        creator_id = payload["sub"]
        expires_at = int(payload["exp"])
        version = int(payload["v"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise FormTokenError("invalid token") from exc
    if version != 1 or not isinstance(creator_id, str) or not creator_id:
        raise FormTokenError("invalid token")
    current_time = _as_utc(now or datetime.now(UTC))
    if expires_at < int(current_time.timestamp()):
        raise FormTokenError("expired token")
    return creator_id


def build_form_url(
    creator_id: str,
    *,
    base_url: str,
    secret: str,
    ttl_days: int = 90,
    now: datetime | None = None,
) -> str:
    token = create_form_token(creator_id, secret=secret, ttl_days=ttl_days, now=now)
    return f"{base_url.rstrip('/')}/?{urlencode({'t': token})}"


def join_creator_pass(contact: ActivityContact, *, now: datetime | None = None) -> dict[str, Any]:
    submitted_at = _as_utc(now or datetime.now(UTC))
    response = dict(contact.form_response_json or {})
    response["form_version"] = FORM_VERSION
    response["joined"] = True
    response.setdefault("joined_at", submitted_at.isoformat())
    response["welcome_bonus_usd"] = WELCOME_BONUS_USD
    response["reward_condition"] = "first_completed_campaign"
    response.setdefault("optional_profile_completed", False)
    _update_reward_breakdown(response)
    contact.form_response_json = response
    contact.form_submitted_at = submitted_at
    if contact.reward_status not in TERMINAL_REWARD_STATUSES:
        contact.reward_status = "pending_first_campaign"
    return response


def update_creator_preferences(
    contact: ActivityContact,
    *,
    collaboration_categories: list[str],
    collaboration_details: dict[str, list[str]],
    other_category: str,
    additional_contact_method: str,
    additional_contact_value: str,
    contact_consent: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    response = dict(contact.form_response_json or {})
    if response.get("joined") is not True:
        raise ValueError("creator must join before saving preferences")
    submitted_at = _as_utc(now or datetime.now(UTC))
    response["form_version"] = FORM_VERSION
    response["collaboration_categories"] = list(collaboration_categories)
    response["collaboration_details"] = {
        category: list(details) for category, details in collaboration_details.items()
    }
    response["other_category"] = other_category
    response["additional_contact_method"] = additional_contact_method
    response["additional_contact_value"] = additional_contact_value
    response["contact_consent"] = contact_consent
    response["optional_profile_completed"] = bool(
        collaboration_categories or other_category or additional_contact_value
    )
    response["preferences_updated_at"] = submitted_at.isoformat()
    _update_reward_breakdown(response)
    contact.form_response_json = response
    contact.form_submitted_at = submitted_at
    return response


def _update_reward_breakdown(response: dict[str, Any]) -> None:
    details = response.get("collaboration_details") or {}
    category_completed = any(bool(values) for values in details.values()) or bool(
        response.get("other_category")
    )
    contact_completed = bool(
        response.get("additional_contact_method")
        and response.get("additional_contact_value")
        and response.get("contact_consent")
    )
    breakdown = {
        "join_plan": WELCOME_BONUS_USD,
        "category_preferences": CATEGORY_BONUS_USD if category_completed else 0,
        "additional_contact": CONTACT_BONUS_USD if contact_completed else 0,
    }
    response["reward_breakdown_usd"] = breakdown
    response["reward_total_usd"] = sum(breakdown.values())


def _sign(encoded_payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), encoded_payload.encode(), hashlib.sha256).digest()
    return _b64encode(digest)


def _validate_secret(secret: str) -> None:
    if len(secret) < 32:
        raise ValueError("FORM_TOKEN_SECRET must contain at least 32 characters")


def _b64encode(value: bytes) -> str:
    return urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return urlsafe_b64decode(value + padding)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
