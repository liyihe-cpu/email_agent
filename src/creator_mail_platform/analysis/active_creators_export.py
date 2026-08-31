from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Callable
from urllib.parse import quote

from sqlalchemy import or_, select
from sqlalchemy.exc import ProgrammingError

from ..core.config import PROJECT_ROOT
from ..core.db import get_session_factory
from ..core.models import ActivityContact, CampaignResponseProfile
from ..outreach.strategies.briefs import load_active_briefs
from .reply_analyzer import analyze_replies, cached_entries
from .workbook import write_active_creator_workbook


DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "一期_已响应达人及回信分析.xlsx"


def export_active_creators(
    *,
    batch_from: int,
    batch_to: int,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    workers: int = 8,
    progress: Callable[[dict[str, int]], None] | None = None,
) -> dict[str, object]:
    if batch_from < 1 or batch_to < batch_from:
        raise ValueError("batch range is invalid")

    initial_cache = cached_entries()
    contacts = _load_contacts(batch_from, batch_to, cached_ids=set(initial_cache))
    analysis_results, failures, cache_hits = analyze_replies(
        contacts,
        workers=workers,
        progress=progress,
    )
    cache = cached_entries()
    cached_ids = set(cache)
    frozen_profiles = _load_frozen_profiles(
        [contact.creator_id for contact in contacts]
    )
    active_contacts = [
        contact
        for contact in contacts
        if contact.form_submitted_at is not None
        or contact.reply_type == "human_reply"
        or contact.creator_id in cached_ids
    ]

    warnings: list[str] = []
    brief_map = {brief.code: brief for brief in load_active_briefs()}
    active_rows: list[dict[str, object]] = []
    reply_rows: list[dict[str, object]] = []

    for contact in active_contacts:
        cache_entry = cache.get(contact.creator_id, {})
        frozen = frozen_profiles.get(contact.creator_id)
        frozen_analysis = (
            frozen.analysis_json.get("initial_response", {})
            if frozen is not None and isinstance(frozen.analysis_json, dict)
            else {}
        )
        analysis = dict(frozen_analysis) if isinstance(frozen_analysis, dict) else {}
        if not analysis:
            analysis = analysis_results.get(contact.creator_id)
        if analysis is None and contact.reply_type != "human_reply":
            cached_analysis = cache_entry.get("analysis")
            analysis = cached_analysis if isinstance(cached_analysis, dict) else {}
        if analysis is None:
            analysis = {}
        snapshot = _frozen_reply_snapshot(frozen) if frozen is not None else _reply_snapshot(contact, cache_entry)
        form = contact.form_response_json or {}
        has_human_reply = (
            bool(snapshot)
            or contact.reply_type == "human_reply"
            or contact.creator_id in cached_ids
        )
        activation_source = _activation_source(
            has_human_reply=has_human_reply,
            has_form=contact.form_submitted_at is not None,
        )
        project_codes = _string_list(analysis.get("interested_project_codes"))
        project_names = [
            brief_map[code].short_name for code in project_codes if code in brief_map
        ]
        project_categories = list(
            dict.fromkeys(
                brief_map[code].primary_category for code in project_codes if code in brief_map
            )
        )
        profile_url = _profile_url(contact.platform, contact.handle)
        base = {
            "creator_id": contact.creator_id,
            "platform": contact.platform,
            "handle": contact.handle,
            "profile_url": profile_url,
            "follower_count": _number(contact.follower_count),
            "email": contact.email,
            "country": contact.country,
            "language": contact.language,
            "primary_category": contact.primary_category,
            "profile_bio": contact.profile_bio,
            "analysis_note": contact.analysis_note,
            "activation_source": activation_source,
            "batch_no": contact.batch_no,
            "last_reply_at": snapshot.get("last_reply_at"),
            "form_submitted_at": contact.form_submitted_at,
            "form_categories": _json_cell(form.get("collaboration_categories") or []),
            "form_category_details": _json_cell(form.get("collaboration_details") or {}),
            "form_other_category": _optional_text(form.get("other_category")),
            "form_contact": _form_contact(form),
            "contact_consent": form.get("contact_consent"),
            "reward_status": contact.reward_status,
            "reply_intent": analysis.get("intent"),
            "interest_source": analysis.get("interest_source"),
            "interested_project_codes": _json_cell(project_codes),
            "interested_projects": _json_cell(project_names),
            "project_primary_categories": _json_cell(project_categories),
            "other_requested_categories": _json_cell(
                analysis.get("other_requested_categories") or []
            ),
            "additional_info": _json_cell(analysis.get("additional_info") or {}),
            "email_feedback": analysis.get("email_feedback"),
            "reply_summary_zh": analysis.get("reply_summary_zh"),
            "followup_status": frozen.status if frozen is not None else None,
            "followup_message_count": len(frozen.messages_json or []) if frozen is not None else 0,
            "followup_last_message_at": frozen.last_message_at if frozen is not None else None,
            "followup_latest_message": _latest_message_text(frozen),
        }
        active_rows.append(base)

        if has_human_reply:
            reply_rows.append(
                {
                    "creator_id": contact.creator_id,
                    "platform": contact.platform,
                    "handle": contact.handle,
                    "batch_no": contact.batch_no,
                    "strategy": _strategy(contact.batch_no),
                    "sent_subject": contact.sent_subject,
                    "sent_body_text": contact.sent_body_text,
                    "sent_brief_code": contact.brief_code,
                    "offered_brief_codes": _json_cell(contact.offered_brief_codes or []),
                    "last_reply_at": snapshot.get("last_reply_at"),
                    "reply_subject": snapshot.get("reply_subject"),
                    "original_reply_text": snapshot.get("reply_body_text"),
                    "intent": analysis.get("intent"),
                    "interest_source": analysis.get("interest_source"),
                    "interested_project_codes": _json_cell(project_codes),
                    "interested_projects": _json_cell(project_names),
                    "project_primary_categories": _json_cell(project_categories),
                    "other_requested_categories": _json_cell(
                        analysis.get("other_requested_categories") or []
                    ),
                    "additional_info": _json_cell(analysis.get("additional_info") or {}),
                    "email_feedback": analysis.get("email_feedback"),
                    "reply_summary_zh": analysis.get("reply_summary_zh"),
                    "followup_status": frozen.status if frozen is not None else None,
                    "followup_message_count": len(frozen.messages_json or []) if frozen is not None else 0,
                    "followup_last_message_at": frozen.last_message_at if frozen is not None else None,
                    "followup_latest_message": _latest_message_text(frozen),
                }
            )

    active_rows.sort(key=lambda row: (int(row["batch_no"]), str(row["creator_id"])))
    reply_rows.sort(key=lambda row: (int(row["batch_no"]), str(row["creator_id"])))
    write_active_creator_workbook(
        output_path,
        active_rows=active_rows,
        reply_rows=reply_rows,
    )
    return {
        "batch_from": batch_from,
        "batch_to": batch_to,
        "active_creators": len(active_rows),
        "reply_analyses": len(reply_rows),
        "human_reply_current": sum(contact.reply_type == "human_reply" for contact in contacts),
        "form_submitted": sum(contact.form_submitted_at is not None for contact in contacts),
        "ai_cache_hits": cache_hits,
        "ai_generated": max(len(analysis_results) - cache_hits, 0),
        "ai_failures": len(failures),
        "failure_details": failures,
        "warnings": warnings,
        "output_path": str(output_path.resolve()),
    }


def _load_contacts(
    batch_from: int,
    batch_to: int,
    *,
    cached_ids: set[str],
) -> list[ActivityContact]:
    active_conditions = [
        ActivityContact.reply_type == "human_reply",
        ActivityContact.form_submitted_at.is_not(None),
    ]
    if cached_ids:
        active_conditions.append(ActivityContact.creator_id.in_(cached_ids))
    session = get_session_factory()()
    try:
        return list(
            session.scalars(
                select(ActivityContact)
                .where(
                    ActivityContact.batch_no >= batch_from,
                    ActivityContact.batch_no <= batch_to,
                    or_(*active_conditions),
                )
                .order_by(ActivityContact.batch_no, ActivityContact.creator_id)
            )
        )
    finally:
        session.close()


def _load_frozen_profiles(
    creator_ids: list[str],
) -> dict[str, CampaignResponseProfile]:
    if not creator_ids:
        return {}
    session = get_session_factory()()
    try:
        return {
            profile.creator_id: profile
            for profile in session.scalars(
                select(CampaignResponseProfile).where(
                    CampaignResponseProfile.creator_id.in_(creator_ids)
                )
            )
        }
    except ProgrammingError:
        # Keep the daily export usable before response profiles are prepared.
        return {}
    finally:
        session.close()


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
    if isinstance(snapshot, dict):
        value = dict(snapshot)
        value["last_reply_at"] = _parse_datetime(value.get("last_reply_at"))
        return value
    return {}


def _frozen_reply_snapshot(
    profile: CampaignResponseProfile,
) -> dict[str, object]:
    message = next(
        (
            item
            for item in profile.messages_json or []
            if item.get("message_kind") == "initial_creator_reply"
        ),
        None,
    )
    if message is None:
        return {}
    return {
        "reply_subject": message.get("subject"),
        "reply_body_text": message.get("body_text"),
        "last_reply_at": _parse_datetime(message.get("occurred_at")),
    }


def _latest_message_text(
    profile: CampaignResponseProfile | None,
) -> str | None:
    if profile is None or not profile.messages_json:
        return None
    message = max(
        profile.messages_json,
        key=lambda item: str(item.get("occurred_at") or ""),
    )
    return _optional_text(message.get("body_text") or message.get("subject"))


def _activation_source(*, has_human_reply: bool, has_form: bool) -> str:
    if has_human_reply and has_form:
        return "human_reply + creator_pass"
    if has_human_reply:
        return "human_reply"
    return "creator_pass"


def _strategy(batch_no: int) -> str | None:
    if 1 <= batch_no <= 25 or batch_no >= 76:
        return "s1"
    if 26 <= batch_no <= 50:
        return "s2"
    if 51 <= batch_no <= 75:
        return "s3"
    return None


def _profile_url(platform: str | None, handle: str | None) -> str | None:
    clean = (handle or "").strip()
    platform_key = (platform or "").strip().casefold()
    prefix = f"{platform_key}:"
    if clean.casefold().startswith(prefix):
        clean = clean[len(prefix) :]
    clean = clean.strip().strip("/").lstrip("@").strip()
    if not clean:
        return None
    encoded = quote(clean, safe="._-")
    if platform_key in {"instagram", "ig"}:
        return f"https://www.instagram.com/{encoded}/"
    if platform_key in {"tiktok", "tik tok"}:
        return f"https://www.tiktok.com/@{encoded}"
    if platform_key in {"youtube", "yt"}:
        return f"https://www.youtube.com/@{encoded}"
    return None


def _form_contact(form: dict[str, object]) -> str | None:
    method = _optional_text(form.get("additional_contact_method"))
    value = _optional_text(form.get("additional_contact_value"))
    if not method or not value:
        return None
    return f"{method}: {value}"


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().upper().replace(",", "")
    match = re.fullmatch(r"([+-]?[0-9]*\.?[0-9]+)\s*([KMB]?)", text)
    if not match:
        return None
    try:
        number = float(match.group(1))
    except ValueError:
        return None
    number *= {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[match.group(2)]
    return int(number) if number.is_integer() else number


def _json_cell(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
