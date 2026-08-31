from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONPATH

from ..core.config import get_settings
from ..core.db import session_scope
from ..core.models import CampaignResponseProfile
from ..core.utils import normalize_email_key
from ..outreach.strategies.base_client import SiliconFlowClient


FOLLOWUP_OUTBOUND_KINDS = {"rejection_notice", "followup_reply"}

REVIEW_PROMPT = """You assist a human COOJOY partnerships manager with replies received after a rejection/update email.

Read the complete chronological conversation. Focus on the creator's latest unquoted message while using earlier messages only as context. Return JSON only:
{
  "reply_required": true,
  "intent": "acknowledgment | question | objection | confusion | future_interest | bd_opportunity | unsubscribe | complaint | other",
  "suggested_action": "reply | no_reply | escalate_bd | manual_review | unsubscribe | close",
  "reason_zh": "one concise Chinese reason for the human reviewer",
  "risk_level": "low | medium | high",
  "language": "language used by the creator",
  "creator_reply_zh": "faithful Chinese translation of only the creator's latest unquoted message",
  "suggested_subject": "email reply subject",
  "suggested_reply": "a concise, warm, context-aware reply in the creator's language",
  "suggested_reply_zh": "faithful Chinese translation of suggested_reply",
  "confidence": 0.0
}

Rules:
- Never claim a campaign, payment, selection, deadline, or guaranteed opportunity unless it already appears in COOJOY's messages.
- If the creator only acknowledges the update, expresses thanks or future interest, and asks nothing, choose no_reply.
- Questions about campaign terms, payment, contracts, personal data, complaints, or a creator who still appears commercially valuable should be surfaced clearly.
- Preserve the existing email language. Keep suggested replies concise and human, normally 2-5 sentences.
- creator_reply_zh must exclude quoted history, signatures, legal footers, and automatic banners.
- suggested_reply_zh is for internal human review only and must accurately mirror suggested_reply.
- Do not include a greeting or signature in reason_zh. suggested_reply must be ready to send.
"""

REVISION_PROMPT = """You revise a COOJOY email reply after a human reviewer gives feedback.

Read the complete chronological conversation, the previous AI draft, and the human instruction. Return JSON only:
{
  "subject": "ready-to-send reply subject",
  "reply": "ready-to-send email body",
  "reply_zh": "faithful Chinese translation of reply"
}

Rules:
- Treat the human instruction as the controlling editing direction. It may be written in Chinese or English.
- Write the final email in the creator's language unless the human explicitly requests another language.
- Preserve accurate details from the conversation. Never invent selection, payment, campaign, deadline, or guarantee claims.
- Keep the reply warm, concise, and natural. Do not quote the earlier email thread.
- Return a complete ready-to-send reply, not editing notes or an explanation.
- reply_zh is for internal review only and must accurately mirror reply.
"""


def pending_review_threads(*, limit: int | None = None) -> list[dict[str, object]]:
    """Return genuine Andy threads that currently require attention."""
    andy = normalize_email_key(get_settings().followup_mailbox_email)
    with session_scope() as session:
        profiles = list(
            session.scalars(
                select(CampaignResponseProfile)
                .where(
                    CampaignResponseProfile.status == "need_reply",
                    func.lower(CampaignResponseProfile.mailbox_email) == andy,
                )
                .order_by(
                    CampaignResponseProfile.last_message_at,
                    CampaignResponseProfile.creator_id,
                )
            )
        )
    rows = [_thread_snapshot(profile) for profile in profiles if _is_genuine_pending(profile)]
    return rows[:limit] if limit is not None else rows


def get_review_thread(creator_id: str) -> dict[str, object]:
    with session_scope() as session:
        profile = session.get(CampaignResponseProfile, creator_id)
        if profile is None or not _is_genuine_pending(profile):
            raise RuntimeError("Thread is not awaiting an Andy follow-up review")
        return _thread_snapshot(profile)


def analyze_followup_thread(
    creator_id: str, *, row: dict[str, object] | None = None
) -> dict[str, object]:
    """Analyze one thread in memory; no AI result is written to PostgreSQL."""
    snapshot = row or get_review_thread(creator_id)
    settings = get_settings()
    payload = SiliconFlowClient(settings).complete_json(
        system_prompt=REVIEW_PROMPT,
        user_prompt=json.dumps(
            {
                "creator": snapshot.get("creator"),
                "existing_initial_reply_analysis": snapshot.get("initial_response"),
                "conversation": snapshot.get("messages"),
            },
            ensure_ascii=False,
        ),
        model=settings.followup_llm_model,
        max_tokens=1200,
        temperature=0.1,
        enable_thinking=True,
    )
    review = _normalize_review(payload, settings.followup_llm_model)
    if not review["creator_reply_zh"]:
        raise RuntimeError("AI returned no Chinese translation for the creator reply")
    if review["suggested_reply"] and not review["suggested_reply_zh"]:
        raise RuntimeError("AI returned no Chinese translation for the suggested reply")
    return review


def revise_followup_reply(
    creator_id: str,
    *,
    instruction: str,
    current_analysis: dict[str, object],
    current_subject: str | None = None,
    current_reply: str | None = None,
) -> dict[str, str]:
    """Revise one draft in memory from human instructions; nothing is saved."""
    instruction = instruction.strip()
    if not instruction:
        raise ValueError("A revision instruction is required")
    row = get_review_thread(creator_id)
    settings = get_settings()
    payload = SiliconFlowClient(settings).complete_json(
        system_prompt=REVISION_PROMPT,
        user_prompt=json.dumps(
            {
                "creator": row.get("creator"),
                "conversation": row.get("messages"),
                "previous_ai_analysis": current_analysis,
                "draft_to_revise": {
                    "subject": current_subject or current_analysis.get("suggested_subject"),
                    "reply": current_reply or current_analysis.get("suggested_reply"),
                },
                "human_instruction": instruction,
            },
            ensure_ascii=False,
        ),
        model=settings.followup_llm_model,
        max_tokens=1000,
        temperature=0.1,
        enable_thinking=True,
    )
    subject = str(payload.get("subject") or "").strip()
    reply = str(payload.get("reply") or "").strip()
    reply_zh = str(payload.get("reply_zh") or "").strip()
    if not reply:
        raise RuntimeError("AI returned an empty revised reply")
    if not reply_zh:
        raise RuntimeError("AI returned no Chinese translation for the revised reply")
    return {"subject": subject, "reply": reply, "reply_zh": reply_zh}


def is_obvious_no_reply(review: dict[str, object]) -> bool:
    """Conservatively auto-resolve only clear, low-risk acknowledgments."""
    try:
        confidence = float(review.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return (
        review.get("reply_required") is False
        and str(review.get("suggested_action") or "").lower() == "no_reply"
        and str(review.get("intent") or "").lower()
        in {"acknowledgment", "future_interest"}
        and str(review.get("risk_level") or "").lower() == "low"
        and confidence >= 0.85
    )


def mark_thread_no_reply(creator_id: str) -> None:
    """Use only the existing status column to remove a thread from the queue."""
    with session_scope() as session:
        profile = session.get(CampaignResponseProfile, creator_id, with_for_update=True)
        if profile is None or not _is_genuine_pending(profile):
            raise RuntimeError("Thread is no longer awaiting review")
        profile.status = "replied"


def collect_followup_stats() -> dict[str, object]:
    """Compute Andy statistics from existing status and message history only."""
    andy = normalize_email_key(get_settings().followup_mailbox_email)
    mailbox_filter = func.lower(CampaignResponseProfile.mailbox_email) == andy
    creator_reply_path = cast('$[*] ? (@.message_kind == "creator_reply")', JSONPATH)
    rejection_sent_path = cast(
        '$[*] ? (@.message_kind == "rejection_notice" && @.delivery_status == "smtp_accepted")',
        JSONPATH,
    )
    latest_kind = CampaignResponseProfile.messages_json[-1]["message_kind"].as_string()
    with session_scope() as session:
        statuses = {
            str(status): int(count)
            for status, count in session.execute(
                select(CampaignResponseProfile.status, func.count())
                .where(mailbox_filter)
                .group_by(CampaignResponseProfile.status)
            )
        }
        totals = session.execute(
            select(
                func.count().label("profiles"),
                func.count()
                .filter(
                    func.jsonb_path_exists(
                        CampaignResponseProfile.messages_json, rejection_sent_path
                    )
                )
                .label("sent_rejections"),
                func.count()
                .filter(
                    func.jsonb_path_exists(
                        CampaignResponseProfile.messages_json, creator_reply_path
                    )
                )
                .label("reply_threads"),
                func.coalesce(
                    func.sum(
                        func.jsonb_array_length(
                            func.jsonb_path_query_array(
                                CampaignResponseProfile.messages_json, creator_reply_path
                            )
                        )
                    ),
                    0,
                ).label("reply_messages"),
                func.count()
                .filter(CampaignResponseProfile.status == "need_reply")
                .label("pending"),
                func.count()
                .filter(
                    CampaignResponseProfile.status == "replied",
                    latest_kind == "creator_reply",
                )
                .label("resolved_without_reply"),
                func.count()
                .filter(
                    CampaignResponseProfile.status == "replied",
                    latest_kind == "followup_reply",
                )
                .label("handled_with_followup"),
            ).where(mailbox_filter)
        ).one()._mapping

    profiles = int(totals["profiles"])
    sent = int(totals["sent_rejections"])
    reply_threads = int(totals["reply_threads"])
    reply_messages = int(totals["reply_messages"])
    pending = int(totals["pending"])
    resolved = int(totals["resolved_without_reply"])
    handled = int(totals["handled_with_followup"])
    return {
        "scope": f"Andy rejection follow-ups only ({andy})",
        "rejection_profiles_total": profiles,
        "status_counts": statuses,
        "rejections_smtp_accepted": sent,
        "rejections_not_smtp_accepted": max(profiles - sent, 0),
        "creators_replied_to_rejection": reply_threads,
        "rejection_reply_rate_pct": _percentage(reply_threads, sent),
        "creator_reply_messages": reply_messages,
        "repeat_reply_messages": max(reply_messages - reply_threads, 0),
        "resolved_without_further_reply": resolved,
        "resolved_rate_among_responders_pct": _percentage(resolved, reply_threads),
        "handled_with_followup_reply": handled,
        "genuine_threads_need_reply": pending,
        "need_reply_rate_among_responders_pct": _percentage(pending, reply_threads),
    }


def _thread_snapshot(profile: CampaignResponseProfile) -> dict[str, object]:
    messages = sorted(
        deepcopy(profile.messages_json or []),
        key=lambda item: str(item.get("occurred_at") or ""),
    )
    latest = _latest_rejection_reply(messages) or {}
    analysis = deepcopy(profile.analysis_json or {})
    return {
        "creator_id": profile.creator_id,
        "batch_no": profile.batch_no,
        "mailbox_email": profile.mailbox_email,
        "recipient_email": profile.recipient_email,
        "last_message_at": profile.last_message_at,
        "latest_message_id": _message_id(latest),
        "latest_message": latest,
        "messages": [_prompt_message(item) for item in messages],
        "creator": analysis.get("creator_profile") or {},
        "initial_response": analysis.get("initial_response") or {},
    }


def _is_genuine_pending(profile: CampaignResponseProfile) -> bool:
    return (
        profile.status == "need_reply"
        and normalize_email_key(profile.mailbox_email)
        == normalize_email_key(get_settings().followup_mailbox_email)
        and _latest_rejection_reply(profile.messages_json or []) is not None
    )


def _latest_rejection_reply(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    outbound = {
        _message_id(item): str(item.get("message_kind") or "")
        for item in messages
        if item.get("direction") == "outbound" and _message_id(item)
    }
    replies = [
        item
        for item in messages
        if item.get("direction") == "inbound"
        and item.get("message_kind") == "creator_reply"
        and outbound.get(str(item.get("in_reply_to") or "").lower())
        in FOLLOWUP_OUTBOUND_KINDS
    ]
    return max(replies, key=lambda item: str(item.get("occurred_at") or ""), default=None)


def _normalize_review(payload: dict[str, object], model: str) -> dict[str, object]:
    action = str(payload.get("suggested_action") or "manual_review").strip().lower()
    if action not in {
        "reply", "no_reply", "escalate_bd", "manual_review", "unsubscribe", "close"
    }:
        action = "manual_review"
    risk = str(payload.get("risk_level") or "medium").strip().lower()
    if risk not in {"low", "medium", "high"}:
        risk = "medium"
    try:
        confidence = max(0.0, min(1.0, float(payload.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "reply_required": bool(payload.get("reply_required")),
        "intent": str(payload.get("intent") or "other").strip(),
        "suggested_action": action,
        "reason_zh": str(payload.get("reason_zh") or "").strip(),
        "risk_level": risk,
        "language": str(payload.get("language") or "English").strip(),
        "creator_reply_zh": str(payload.get("creator_reply_zh") or "").strip(),
        "suggested_subject": str(payload.get("suggested_subject") or "").strip(),
        "suggested_reply": str(payload.get("suggested_reply") or "").strip(),
        "suggested_reply_zh": str(payload.get("suggested_reply_zh") or "").strip(),
        "confidence": confidence,
        "model": model,
    }


def _prompt_message(item: dict[str, Any]) -> dict[str, object]:
    return {
        "occurred_at": item.get("occurred_at"),
        "direction": item.get("direction"),
        "message_kind": item.get("message_kind"),
        "subject": item.get("subject"),
        "body_text": item.get("body_text"),
    }


def _message_id(message: dict[str, Any]) -> str:
    return str(message.get("message_id") or "").strip().lower()


def _percentage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator * 100 / denominator, 2)
