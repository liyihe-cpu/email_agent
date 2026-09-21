from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import UTC, datetime
import json
import re
from typing import Any

from sqlalchemy import select

from ..core.config import get_settings
from ..core.db import session_scope
from ..core.models import CampaignResponseProfile
from ..outreach.strategies.base_client import SiliconFlowClient
from ..outreach.strategies.localization import LANGUAGE_NAMES, resolve_language_code


LOCALIZED_CODES = {"es", "pt", "fr", "de", "ja", "ko", "ar"}

APOLOGY_BACKFILL_VERSION = "sincere-apology-v1"
APOLOGY_SENTENCES = {
    "en": "We're really sorry that we won't be able to work together on this opportunity.",
    "es": "Sentimos mucho no poder trabajar juntos en esta oportunidad.",
    "pt": "Lamentamos muito não poder trabalhar juntos nesta oportunidade.",
    "fr": "Nous sommes vraiment désolés de ne pas pouvoir travailler ensemble sur cette opportunité.",
    "de": "Es tut uns wirklich leid, dass wir bei dieser Gelegenheit nicht zusammenarbeiten können.",
    "ja": "今回の機会にご一緒できず、本当に申し訳ございません。",
    "ko": "이번 기회에 함께하지 못하게 되어 진심으로 죄송합니다.",
    "ar": "نأسف حقًا لعدم تمكننا من العمل معًا في هذه الفرصة.",
}
APOLOGY_MARKERS = {
    "en": ("really sorry", "sincerely sorry", "our apologies", "we apologize"),
    "es": ("sentimos", "lamentamos", "lo sentimos", "pedimos disculpas"),
    "pt": ("lamentamos", "sentimos", "desculp"),
    "fr": ("désol", "regrettons", "nos excuses"),
    "de": ("leid", "bedauern", "entschuldigen"),
    "ja": ("申し訳",),
    "ko": ("죄송",),
    "ar": ("نأسف", "نعتذر", "آسفون"),
}

REJECTION_SYSTEM_PROMPT = """You are a thoughtful creator partnerships manager at COOJOY.

Write a personalized campaign update for a creator whose reply was classified as positive.
The business decision is already final: COOJOY cannot move forward with this creator in the
current campaign round. Your job is to communicate that decision with warmth and precision,
not to reconsider it.

Context rules:
- Read the original COOJOY outreach, the creator's own reply, and the structured analysis.
- Personalization here means recognizing the creator's interest, not summarizing their reply.
- In the opening, mention at most one project code or refer collectively to the opportunities
  they selected. Do not repeat their rate, availability, contact details, platform, location,
  or a list of projects.
- If several projects were selected, refer collectively to "the opportunities you expressed
  interest in" instead of rejecting every project one by one.
- Frame the decision as a brand-side campaign update: the brand has finalized its creator
  lineup and the available slots for this round have been filled. Do not claim the brand
  personally reviewed this creator.
- Mention that the brand reduced slots, scope, or budget only when that exact fact is explicitly
  supplied in business_facts. Never invent a budget cut or changed campaign scope.
- Include one brief, sincere apology in the decision sentence. In English, prefer the warmer
  construction: "We're really sorry that we won't be able to move forward together this time,
  as the brand has finalized its creator lineup and the available spots for this round have
  been filled." Avoid the formulaic
  "We're sorry, but..." construction. Use an equally natural apology in the creator's localized
  language.
- Say that COOJOY has kept the creator's profile and collaboration preferences in its active
  creator network and will prioritize contacting and recommending them for suitable future
  matches. Do not promise that a specific campaign will be secured or give a specific timeline.
- If the creator asked a question, answer it only when the supplied context contains the answer.
  Otherwise close the current round politely without inventing information.

Tone rules:
- Human, appreciative, calm, and concise; sound like an experienced partnerships manager.
- Deliver the decision in the opening paragraph: acknowledge the creator in one short sentence,
  then immediately state that the brand has completed its creator lineup for the current round.
- Include exactly one apology. Do not stack "sorry," "unfortunately," and repeated expressions
  of regret in the same message.
- Do not spend a full paragraph thanking the creator or restating every detail they supplied.
- Use no more than three short paragraphs before the sign-off.
- Avoid filler closings such as "wishing you a great month," "continued success," or
  "looking forward to working together."
- Adapt naturally to the creator's level of enthusiasm. Do not over-praise or sound dramatic.
- Never use words such as rejected, failed, unsuitable, unqualified, or not good enough.
- The subject is for a brand-new email thread. Never begin it with Re: or Fwd:, and do not copy
  the original outreach subject.
- Do not mention AI, internal scoring, intent labels, databases, selection logic, or prompts.
- Do not use markdown bullets. Include greeting, short paragraphs, sign-off, and COOJOY website.
- English body target: 45-80 words. Localized body should carry the same meaning naturally
  and remain equally concise.

Language rules:
- For English, return only body_english and set body_primary to null.
- For Spanish, Portuguese, French, German, Japanese, Korean, or Arabic, write a natural local
  version in body_primary and an English version in body_english.
- For every other language, use English only.
- Keep project codes, brand names, URLs, and money formats unchanged.

Return JSON only:
{
  "subject": "natural email subject",
  "body_primary": null,
  "body_english": "complete English email",
  "acknowledged_points": ["short factual point"],
  "generation_reason": "one short internal explanation"
}
"""


def generate_rejection_drafts(
    *,
    limit: int = 5,
    offset: int = 0,
    workers: int = 3,
    apply: bool = False,
    newest_first: bool = False,
) -> dict[str, object]:
    """Generate personalized S1/S3 rejection drafts; never sends email."""
    settings = get_settings()
    model = settings.followup_llm_model.strip()
    if not model:
        raise RuntimeError("FOLLOWUP_LLM_MODEL is not configured")
    candidates = _load_candidates(
        limit,
        offset=offset,
        skip_generated=apply,
        newest_first=newest_first,
    )
    if not candidates:
        return {
            "model": model,
            "selected": 0,
            "generated": 0,
            "failed": 0,
            "applied": apply,
            "previews": [],
            "failures": [],
        }

    def work(profile: CampaignResponseProfile) -> dict[str, object]:
        client = SiliconFlowClient(settings)
        language = _target_language(profile)
        payload = client.complete_json(
            model=model,
            system_prompt=REJECTION_SYSTEM_PROMPT,
            user_prompt=_user_prompt(profile, language),
            max_tokens=1_200,
            temperature=0.35,
            enable_thinking=None,
        )
        return _normalize_result(profile, language, model, payload)

    generated: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(candidates)))) as executor:
        future_map = {executor.submit(work, profile): profile.creator_id for profile in candidates}
        for future in as_completed(future_map):
            creator_id = future_map[future]
            try:
                generated.append(future.result())
            except Exception as exc:
                failures.append(
                    {"creator_id": creator_id, "error": f"{type(exc).__name__}: {exc}"}
                )

    generated.sort(key=lambda item: str(item["creator_id"]))
    if apply and generated:
        _apply_drafts(generated)
    return {
        "model": model,
        "selected": len(candidates),
        "generated": len(generated),
        "failed": len(failures),
        "applied": apply,
        "previews": generated,
        "failures": failures,
    }


def _load_candidates(
    limit: int,
    *,
    offset: int = 0,
    skip_generated: bool = False,
    newest_first: bool = False,
) -> list[CampaignResponseProfile]:
    with session_scope() as session:
        profiles = list(
            session.scalars(
                select(CampaignResponseProfile)
                .where(CampaignResponseProfile.status == "draft")
                .order_by(CampaignResponseProfile.creator_id)
            )
        )
    eligible = [
        profile
        for profile in profiles
        if is_rejection_eligible(profile)
        and (not skip_generated or _generated_rejection_draft(profile) is None)
    ]
    eligible.sort(key=_initial_reply_sort_key, reverse=newest_first)
    return eligible[offset : offset + limit]


def is_rejection_eligible(profile: CampaignResponseProfile) -> bool:
    batch = profile.batch_no or 0
    if not (1 <= batch <= 25 or 51 <= batch <= 75 or batch >= 76):
        return False
    analysis = profile.analysis_json.get("initial_response", {})
    if not isinstance(analysis, dict) or analysis.get("intent") != "positive":
        return False
    project_codes = _string_list(analysis.get("interested_project_codes"))
    if not project_codes:
        return False
    return _initial_reply(profile) is not None


def _user_prompt(profile: CampaignResponseProfile, language: str) -> str:
    messages = [
        message
        for message in profile.messages_json or []
        if message.get("message_kind") != "rejection_notice"
    ]
    analysis = profile.analysis_json if isinstance(profile.analysis_json, dict) else {}
    return json.dumps(
        {
            "creator": analysis.get("creator_profile", {}),
            "target_language": LANGUAGE_NAMES.get(language, "English"),
            "original_outreach": _message(profile, "initial_outreach"),
            "creator_reply": _clean_reply(_initial_reply(profile)),
            "reply_analysis": analysis.get("initial_response", {}),
            "conversation_so_far": messages,
            "business_facts": {
                "current_round_status": "the brand has finalized its creator lineup and the available creator slots for this round have been filled",
                "budget_or_scope_reduction_confirmed": False,
                "future_status": "retain in the active creator network and prioritize contacting and recommending the creator for suitable future matches",
            },
        },
        ensure_ascii=False,
        default=str,
    )


def _normalize_result(
    profile: CampaignResponseProfile,
    language: str,
    model: str,
    payload: dict[str, object],
) -> dict[str, object]:
    subject = _required_text(payload.get("subject"), "subject")
    subject = re.sub(r"^(?:re|fwd?)\s*:\s*", "", subject, flags=re.IGNORECASE)
    english = _required_text(payload.get("body_english"), "body_english")
    primary = _optional_text(payload.get("body_primary"))
    if language in LOCALIZED_CODES and not primary:
        raise ValueError(f"AI omitted the {language} localized body")
    final_body = (
        f"{primary}\n\n----------------------------------------\n\n{english}"
        if primary
        else english
    )
    reply = _clean_reply(_initial_reply(profile)) or {}
    analysis = profile.analysis_json.get("initial_response", {})
    return {
        "creator_id": profile.creator_id,
        "batch_no": profile.batch_no,
        "handle": profile.analysis_json.get("creator_profile", {}).get("handle"),
        "language": language,
        "model": model,
        "original_reply_subject": reply.get("subject"),
        "original_reply_body": reply.get("body_text"),
        "interested_project_codes": (
            analysis.get("interested_project_codes", [])
            if isinstance(analysis, dict)
            else []
        ),
        "subject": subject,
        "body_text": final_body,
        "acknowledged_points": _string_list(payload.get("acknowledged_points")),
        "generation_reason": _optional_text(payload.get("generation_reason")),
    }


def _apply_drafts(results: list[dict[str, object]]) -> None:
    now = datetime.now(UTC)
    with session_scope() as session:
        for result in results:
            profile = session.get(
                CampaignResponseProfile,
                str(result["creator_id"]),
                with_for_update=True,
            )
            if profile is None or profile.status != "draft":
                continue
            # JSONB values are not deeply mutable-tracked by SQLAlchemy. Work on
            # a detached copy so assigning it back always marks the column dirty.
            messages = deepcopy(profile.messages_json or [])
            draft = next(
                (
                    item
                    for item in reversed(messages)
                    if item.get("message_kind") == "rejection_notice"
                    and item.get("delivery_status") in {"draft", "temporary_failed"}
                ),
                None,
            )
            if draft is None:
                continue
            draft["subject"] = result["subject"]
            draft["body_text"] = result["body_text"]
            draft["delivery_status"] = "draft"
            draft["status_reason"] = None
            draft["metadata"] = {
                "generated_by": result["model"],
                "acknowledged_points": result["acknowledged_points"],
                "generation_reason": result["generation_reason"],
            }
            profile.messages_json = messages
            profile.updated_at = now


def backfill_rejection_apologies(
    *, limit: int | None = None, apply: bool = False
) -> dict[str, object]:
    """Add one localized apology to unsent AI drafts without regenerating them."""
    with session_scope() as session:
        profiles = list(
            session.scalars(
                select(CampaignResponseProfile)
                .where(CampaignResponseProfile.status == "draft")
                .order_by(
                    CampaignResponseProfile.last_message_at.desc().nullslast(),
                    CampaignResponseProfile.creator_id,
                )
            )
        )

    candidates: list[dict[str, object]] = []
    already_present = 0
    for profile in profiles:
        if not is_rejection_eligible(profile):
            continue
        draft = _generated_rejection_draft(profile)
        if draft is None:
            continue
        metadata = draft.get("metadata") or {}
        if metadata.get("apology_backfill_version") == APOLOGY_BACKFILL_VERSION:
            already_present += 1
            continue
        language = _target_language(profile)
        original = str(draft.get("body_text") or "").strip()
        revised = _add_apology_to_body(original, language)
        if revised == original:
            already_present += 1
            continue
        candidates.append(
            {
                "creator_id": profile.creator_id,
                "handle": (profile.analysis_json.get("creator_profile") or {}).get("handle"),
                "language": language,
                "subject": draft.get("subject"),
                "before": original,
                "after": revised,
            }
        )

    if limit is not None:
        candidates = candidates[:limit]
    if apply and candidates:
        _apply_apology_backfill(candidates)
    return {
        "selected": len(candidates),
        "already_present": already_present,
        "applied": apply,
        "updated": len(candidates) if apply else 0,
        "previews": candidates[:5],
    }


def _generated_rejection_draft(
    profile: CampaignResponseProfile,
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in reversed(profile.messages_json or [])
            if item.get("message_kind") == "rejection_notice"
            and item.get("delivery_status") in {"draft", "temporary_failed"}
            and bool((item.get("metadata") or {}).get("generated_by"))
        ),
        None,
    )


def _add_apology_to_body(body: str, language: str) -> str:
    separator = "\n\n----------------------------------------\n\n"
    if language in LOCALIZED_CODES and separator in body:
        primary, english = body.split(separator, 1)
        return separator.join(
            (
                _add_apology_to_section(primary, language),
                _add_apology_to_section(english, "en"),
            )
        )
    return _add_apology_to_section(body, "en")


def _add_apology_to_section(section: str, language: str) -> str:
    if language == "en":
        strengthened = re.sub(
            r"\bWe(?:'|’)re sorry that\b",
            "We're really sorry that",
            section,
            flags=re.IGNORECASE,
        )
        if strengthened != section:
            return strengthened
    lowered = section.casefold()
    if any(marker.casefold() in lowered for marker in APOLOGY_MARKERS[language]):
        return section
    paragraphs = re.split(r"\n\s*\n", section.strip())
    target = 1 if len(paragraphs) > 1 else 0
    paragraphs[target] = f"{paragraphs[target].rstrip()} {APOLOGY_SENTENCES[language]}"
    return "\n\n".join(paragraphs)


def _apply_apology_backfill(candidates: list[dict[str, object]]) -> None:
    now = datetime.now(UTC)
    with session_scope() as session:
        for candidate in candidates:
            profile = session.get(
                CampaignResponseProfile,
                str(candidate["creator_id"]),
                with_for_update=True,
            )
            if profile is None or profile.status != "draft":
                continue
            messages = deepcopy(profile.messages_json or [])
            draft = next(
                (
                    item
                    for item in reversed(messages)
                    if item.get("message_kind") == "rejection_notice"
                    and item.get("delivery_status") in {"draft", "temporary_failed"}
                    and bool((item.get("metadata") or {}).get("generated_by"))
                ),
                None,
            )
            if draft is None:
                continue
            metadata = dict(draft.get("metadata") or {})
            if metadata.get("apology_backfill_version") == APOLOGY_BACKFILL_VERSION:
                continue
            current = str(draft.get("body_text") or "").strip()
            language = _target_language(profile)
            revised = _add_apology_to_body(current, language)
            if revised == current:
                continue
            draft["body_text"] = revised
            metadata["apology_backfill_version"] = APOLOGY_BACKFILL_VERSION
            metadata["apology_backfilled_at"] = now.isoformat()
            draft["metadata"] = metadata
            profile.messages_json = messages
            profile.updated_at = now


def _target_language(profile: CampaignResponseProfile) -> str:
    creator = profile.analysis_json.get("creator_profile", {})
    raw = creator.get("language") if isinstance(creator, dict) else None
    code = resolve_language_code(str(raw) if raw else None)
    return code if code in LOCALIZED_CODES else "en"


def _initial_reply(profile: CampaignResponseProfile) -> dict[str, Any] | None:
    return _message(profile, "initial_creator_reply")


def _initial_reply_sort_key(
    profile: CampaignResponseProfile,
) -> tuple[datetime, int, str]:
    reply = _initial_reply(profile) or {}
    raw_time = str(reply.get("occurred_at") or "").strip()
    try:
        reply_time = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        if reply_time.tzinfo is None:
            reply_time = reply_time.replace(tzinfo=UTC)
        else:
            reply_time = reply_time.astimezone(UTC)
    except ValueError:
        reply_time = profile.last_message_at or datetime.max.replace(tzinfo=UTC)
    return reply_time, profile.batch_no or 0, profile.creator_id


def _message(profile: CampaignResponseProfile, kind: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in profile.messages_json or []
            if item.get("message_kind") == kind
        ),
        None,
    )


def _clean_reply(message: dict[str, Any] | None) -> dict[str, Any] | None:
    if message is None:
        return None
    cleaned = dict(message)
    body = str(message.get("body_text") or "")
    kept: list[str] = []
    quote_markers = (
        re.compile(r"^On .+wrote:\s*$", re.IGNORECASE),
        re.compile(r"^El .+escribi[oó]:\s*$", re.IGNORECASE),
        re.compile(r"^Le .+[ée]crit\s*:\s*$", re.IGNORECASE),
        re.compile(r"^Am .+schrieb .+:\s*$", re.IGNORECASE),
        re.compile(r"^-{2,}\s*Original Message\s*-{2,}$", re.IGNORECASE),
    )
    for line in body.splitlines():
        stripped = line.strip()
        if kept and (
            stripped.startswith(">")
            or any(pattern.match(stripped) for pattern in quote_markers)
        ):
            break
        kept.append(line)
    cleaned["body_text"] = "\n".join(kept).strip()
    return cleaned


def _required_text(value: object, field: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"AI returned an empty {field}")
    return text


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
