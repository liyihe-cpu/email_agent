from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
from threading import local
from typing import Callable, cast

from ..core.config import PROJECT_ROOT
from ..core.models import ActivityContact
from ..core.taxonomy import COLLABORATION_CATEGORIES
from ..outreach.strategies.base_client import SiliconFlowClient
from ..outreach.strategies.briefs import CampaignBrief, load_active_briefs


CACHE_PATH = PROJECT_ROOT / "runtime" / "reply_analysis_cache.json"
CACHE_VERSION = 1
INTENTS = {"positive", "negative", "question"}

REPLY_ANALYSIS_SYSTEM_PROMPT = """You analyze creator replies to COOJOY outreach emails.

Return JSON only with exactly these fields:
{
  "intent": "positive | negative | question",
  "interested_project_codes": ["PROJECT-CODE"],
  "other_requested_categories": ["one approved primary category"],
  "additional_info": {},
  "email_feedback": null,
  "reply_summary_zh": "one concise Chinese sentence"
}

Rules:
- Analyze only text written by the creator. Ignore quoted email history, the original outreach, signatures, legal footers, and automatic banners.
- positive: the creator explicitly wants to collaborate, supplies a rate or availability, or clearly accepts an offered project.
- negative: the creator declines, is not interested, or asks not to be contacted.
- question: the creator asks for details without accepting, or the intent is otherwise unclear. A generic thank-you alone is question.
- Add a project code only when the creator's own reply explicitly shows interest in that project. Never infer interest merely because COOJOY offered it.
- other_requested_categories contains only collaboration categories the creator explicitly asks for beyond the interested projects.
- additional_info is an unrestricted JSON object. Extract any explicit cooperation-useful facts, such as rates and currency, availability, contact details, manager/agency, preferred platform or deliverable, usage-rights or payment questions, shipping/location facts, or future collaboration preferences. Do not invent facts.
- email_feedback is a short Chinese summary only when the creator explicitly comments on or suggests improvements to COOJOY's outreach format. Generic praise or thanks is null.
- reply_summary_zh is factual and concise. Do not add confidence scores or review flags.
"""


def analyze_replies(
    contacts: list[ActivityContact],
    *,
    workers: int = 8,
    cache_path: Path = CACHE_PATH,
    progress: Callable[[dict[str, int]], None] | None = None,
) -> tuple[dict[str, dict[str, object]], list[dict[str, str]], int]:
    """Analyze changed human replies and reuse unchanged local results."""
    cache = _load_cache(cache_path)
    entries = cast(dict[str, object], cache["entries"])
    briefs = load_active_briefs()
    brief_map = {brief.code: brief for brief in briefs}
    selected = [contact for contact in contacts if contact.reply_type == "human_reply"]
    results: dict[str, dict[str, object]] = {}
    pending: list[tuple[ActivityContact, str]] = []
    cache_changed = False

    for contact in selected:
        fingerprint = reply_fingerprint(contact)
        cached = entries.get(contact.creator_id)
        if isinstance(cached, dict) and cached.get("fingerprint") == fingerprint:
            analysis = cached.get("analysis")
            if isinstance(analysis, dict):
                enriched = _apply_interest_attribution(contact, analysis, brief_map)
                if enriched != analysis:
                    cached["analysis"] = enriched
                    analysis = enriched
                    cache_changed = True
                if not isinstance(cached.get("reply_snapshot"), dict):
                    cached["reply_snapshot"] = _reply_snapshot(contact)
                    cache_changed = True
                results[contact.creator_id] = analysis
                continue
        pending.append((contact, fingerprint))

    if cache_changed:
        _save_cache(cache, cache_path)

    failures: list[dict[str, str]] = []
    completed = len(results)
    if progress:
        progress({"total": len(selected), "completed": completed, "generated": 0})

    generated = 0
    thread_state = local()

    def client_for_thread() -> SiliconFlowClient:
        client = getattr(thread_state, "client", None)
        if client is None:
            client = SiliconFlowClient()
            thread_state.client = client
        return client

    def work(item: tuple[ActivityContact, str]) -> tuple[str, str, dict[str, object]]:
        contact, fingerprint = item
        payload = client_for_thread().complete_json(
            system_prompt=REPLY_ANALYSIS_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(contact, briefs),
            max_tokens=900,
        )
        return contact.creator_id, fingerprint, _normalize_analysis(payload, brief_map)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_map = {executor.submit(work, item): item[0] for item in pending}
        for future in as_completed(future_map):
            contact = future_map[future]
            creator_id = contact.creator_id
            try:
                creator_id, fingerprint, analysis = future.result()
                analysis = _apply_interest_attribution(contact, analysis, brief_map)
                results[creator_id] = analysis
                entries[creator_id] = {
                    "fingerprint": fingerprint,
                    "analysis": analysis,
                    "reply_snapshot": _reply_snapshot(contact),
                }
                generated += 1
                if generated % 20 == 0:
                    _save_cache(cache, cache_path)
            except Exception as exc:  # one bad reply must not block the export
                failures.append({"creator_id": creator_id, "error": f"{type(exc).__name__}: {exc}"})
            completed += 1
            if progress:
                progress({"total": len(selected), "completed": completed, "generated": generated})

    if generated:
        _save_cache(cache, cache_path)
    return results, failures, len(selected) - len(pending)


def cached_creator_ids(cache_path: Path = CACHE_PATH) -> set[str]:
    entries = cast(dict[str, object], _load_cache(cache_path)["entries"])
    return {str(value) for value in entries}


def cached_entries(cache_path: Path = CACHE_PATH) -> dict[str, dict[str, object]]:
    entries = cast(dict[str, object], _load_cache(cache_path)["entries"])
    return {
        str(creator_id): value
        for creator_id, value in entries.items()
        if isinstance(value, dict)
    }


def reply_fingerprint(contact: ActivityContact) -> str:
    source = {
        "sent_subject": contact.sent_subject,
        "sent_body_text": contact.sent_body_text,
        "offered_brief_codes": contact.offered_brief_codes,
        "reply_subject": contact.reply_subject,
        "reply_body_text": contact.reply_body_text,
        "last_reply_at": contact.last_reply_at.isoformat() if contact.last_reply_at else None,
    }
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reply_snapshot(contact: ActivityContact) -> dict[str, object]:
    return {
        "reply_subject": contact.reply_subject,
        "reply_body_text": contact.reply_body_text,
        "last_reply_at": contact.last_reply_at.isoformat() if contact.last_reply_at else None,
    }


def _build_user_prompt(contact: ActivityContact, briefs: list[CampaignBrief]) -> str:
    offered = set(contact.offered_brief_codes or [])
    if contact.brief_code:
        offered.add(contact.brief_code)
    brief_context = [
        {
            "code": brief.code,
            "name": brief.short_name,
            "primary_category": brief.primary_category,
        }
        for brief in briefs
        if brief.code in offered
    ]
    return json.dumps(
        {
            "creator": {
                "handle": contact.handle,
                "profile_category": contact.primary_category,
                "country": contact.country,
                "language": contact.language,
            },
            "approved_primary_categories": list(COLLABORATION_CATEGORIES),
            "offered_projects": brief_context,
            "outbound_email": {
                "subject": contact.sent_subject,
                "body": (contact.sent_body_text or "")[:8_000],
            },
            "creator_reply": {
                "subject": contact.reply_subject,
                "body": (contact.reply_body_text or "")[:12_000],
            },
        },
        ensure_ascii=False,
    )


def _normalize_analysis(
    payload: dict[str, object],
    brief_map: dict[str, CampaignBrief],
) -> dict[str, object]:
    intent = str(payload.get("intent") or "question").strip().lower()
    if intent not in INTENTS:
        intent = "question"

    project_codes = _string_list(payload.get("interested_project_codes"))
    project_codes = list(dict.fromkeys(code for code in project_codes if code in brief_map))
    requested = _string_list(payload.get("other_requested_categories"))
    requested = list(
        dict.fromkeys(value for value in requested if value in COLLABORATION_CATEGORIES)
    )
    additional_info = payload.get("additional_info")
    if not isinstance(additional_info, dict):
        additional_info = {}

    return {
        "intent": intent,
        "interested_project_codes": project_codes,
        "interested_projects": [brief_map[code].short_name for code in project_codes],
        "project_primary_categories": list(
            dict.fromkeys(brief_map[code].primary_category for code in project_codes)
        ),
        "other_requested_categories": requested,
        "additional_info": additional_info,
        "email_feedback": _optional_text(payload.get("email_feedback")),
        "reply_summary_zh": _optional_text(payload.get("reply_summary_zh")) or "未提取到明确摘要",
    }


def _apply_interest_attribution(
    contact: ActivityContact,
    analysis: dict[str, object],
    brief_map: dict[str, CampaignBrief],
) -> dict[str, object]:
    """Attribute a generic positive S1/S3 reply to its featured offer.

    The model remains responsible for semantic intent and explicit project
    mentions. This deterministic step only fills the one or two projects that
    were featured in the outbound email; compact-list projects are never
    inferred as interests.
    """
    result = dict(analysis)
    intent = str(result.get("intent") or "question").strip().lower()
    project_codes = list(
        dict.fromkeys(
            code
            for code in _string_list(result.get("interested_project_codes"))
            if code in brief_map
        )
    )
    previous_source = str(result.get("interest_source") or "").strip()

    if previous_source == "inferred_from_featured_offer":
        project_codes = _featured_offer_codes(contact, brief_map)
        interest_source: str | None = (
            "inferred_from_featured_offer" if project_codes else "general_collaboration"
        )
    elif project_codes:
        interest_source = "explicit"
    elif intent == "positive":
        project_codes = _featured_offer_codes(contact, brief_map)
        interest_source = (
            "inferred_from_featured_offer" if project_codes else "general_collaboration"
        )
    else:
        interest_source = None

    result["interested_project_codes"] = project_codes
    result["interested_projects"] = [brief_map[code].short_name for code in project_codes]
    result["project_primary_categories"] = list(
        dict.fromkeys(brief_map[code].primary_category for code in project_codes)
    )
    result["interest_source"] = interest_source
    return result


def _featured_offer_codes(
    contact: ActivityContact,
    brief_map: dict[str, CampaignBrief],
) -> list[str]:
    if not (
        1 <= contact.batch_no <= 25
        or 51 <= contact.batch_no <= 75
        or contact.batch_no >= 76
    ):
        return []

    offered = list(dict.fromkeys(contact.offered_brief_codes or []))
    offered_set = set(offered)
    primary = str(contact.brief_code or "").strip()
    if primary not in brief_map or (offered_set and primary not in offered_set):
        return []

    codes = [primary]
    if not brief_map[primary].is_fallback:
        fallback = next(
            (
                code
                for code in offered
                if code in brief_map and brief_map[code].is_fallback
            ),
            None,
        )
        if fallback:
            codes.append(fallback)
    return codes


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _load_cache(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"version": CACHE_VERSION, "entries": {}}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": CACHE_VERSION, "entries": {}}
    if document.get("version") != CACHE_VERSION or not isinstance(document.get("entries"), dict):
        return {"version": CACHE_VERSION, "entries": {}}
    return document


def _save_cache(cache: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
