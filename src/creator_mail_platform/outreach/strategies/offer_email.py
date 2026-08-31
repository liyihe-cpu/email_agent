from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from .briefs import CampaignBrief
from .localization import resolve_language_code
from .offer_copy import (
    OFFER_LOCALIZED_COPIES,
    REPLY_EXAMPLE,
    OfferCopy,
    select_english_offer_copy,
)


MIN_MATCH_SCORE = 0.65


@dataclass(frozen=True)
class ProjectOffer:
    primary: CampaignBrief | None
    fallback: CampaignBrief
    compact: tuple[CampaignBrief, ...]

    @property
    def featured(self) -> tuple[CampaignBrief, ...]:
        if self.primary is None:
            return (self.fallback,)
        return (self.primary, self.fallback)

    @property
    def offered_codes(self) -> tuple[str, ...]:
        return tuple(brief.code for brief in (*self.featured, *self.compact))


def build_project_offer(
    briefs: list[CampaignBrief],
    *,
    country: str | None,
    platform: str | None = None,
    selected_code: str | None,
    match_score: float,
) -> ProjectOffer:
    eligible = [
        brief
        for brief in briefs
        if _region_matches(brief, country) and _platform_matches(brief, platform)
    ]
    fallback = next(
        (brief for brief in eligible if brief.is_fallback),
        None,
    )
    if fallback is None:
        raise RuntimeError("An eligible fallback Brief is required")

    primary = next(
        (
            brief
            for brief in eligible
            if not brief.is_fallback
            and brief.code == (selected_code or "").strip()
            and match_score >= MIN_MATCH_SCORE
        ),
        None,
    )
    featured_codes = {brief.code for brief in (primary, fallback) if brief is not None}
    compact = tuple(brief for brief in eligible if brief.code not in featured_codes)
    return ProjectOffer(primary=primary, fallback=fallback, compact=compact)


def matching_candidates(
    briefs: list[CampaignBrief],
    *,
    country: str | None,
    platform: str | None = None,
) -> list[CampaignBrief]:
    return [
        brief
        for brief in briefs
        if not brief.is_fallback
        and _region_matches(brief, country)
        and _platform_matches(brief, platform)
    ]


def render_offer_email(
    *,
    creator_key: str,
    handle: str,
    raw_language: str | None,
    primary_opening: str,
    english_opening: str,
    offer: ProjectOffer,
    creator_pass_url: str | None = None,
) -> tuple[str, str, str]:
    subject_brief = offer.primary or offer.fallback
    subject = _select_subject(subject_brief, creator_key=creator_key, handle=handle)
    language_code = resolve_language_code(raw_language)
    english = _render_offer_section(
        select_english_offer_copy(creator_key),
        handle=handle,
        personalized_opening=english_opening,
        offer=offer,
        creator_pass_url=creator_pass_url,
        include_reply_example=True,
    )
    if language_code == "en":
        return subject, english, language_code

    copy = OFFER_LOCALIZED_COPIES[language_code]
    primary = _render_offer_section(
        copy,
        handle=handle,
        personalized_opening=primary_opening or english_opening,
        offer=offer,
        creator_pass_url=creator_pass_url,
        include_reply_example=True,
    )
    return (
        subject,
        f"{primary}\n\n---------- English ----------\n\n{english}",
        language_code,
    )


def _select_subject(brief: CampaignBrief, *, creator_key: str, handle: str) -> str:
    subjects = brief.subjects
    if not subjects:
        raise RuntimeError(f"Brief {brief.code} has no subject")
    digest = hashlib.sha256(f"{creator_key}:{brief.code}".encode("utf-8")).digest()
    template = subjects[digest[0] % len(subjects)]
    return template.format(handle=handle)


def _render_offer_section(
    copy: OfferCopy,
    *,
    handle: str,
    personalized_opening: str,
    offer: ProjectOffer,
    creator_pass_url: str | None,
    include_reply_example: bool,
) -> str:
    intro = copy.matched_intro if offer.primary is not None else copy.fallback_intro
    featured_heading = (
        copy.featured_many if len(offer.featured) > 1 else copy.featured_one
    )
    sections = [
        copy.greeting.format(handle=handle),
        copy.courtesy,
        copy.team_intro,
        personalized_opening.strip(),
        intro,
        featured_heading,
        "\n\n".join(
            _featured_block(brief, copy=copy) for brief in offer.featured
        ),
    ]
    if offer.compact:
        sections.extend(
            [
                copy.other_heading,
                "\n".join(_compact_line(brief) for brief in offer.compact),
            ]
        )
    sections.append(copy.reply_cta)
    if include_reply_example:
        sections.append(REPLY_EXAMPLE)
    if creator_pass_url:
        sections.append(copy.creator_pass_note.format(url=creator_pass_url))
    sections.append(copy.closing)
    sections.append(
        "Best regards,\nCOOJOY Creator Partnership Team\nhttps://www.coojoy.cn"
    )
    return "\n\n".join(sections)


def _featured_block(brief: CampaignBrief, *, copy: OfferCopy) -> str:
    return "\n".join(
        [
            f"{brief.code} — {brief.short_name}",
            f"- {copy.product_label}: {brief.product_name}",
            f"- {copy.deliverable_label}: {brief.deliverables}",
            f"- {copy.compensation_label}: {brief.compensation}",
            f"- {copy.timeline_label}: {brief.timeline}",
            f"- {copy.usage_rights_label}: {brief.usage_rights}",
        ]
    )


def _compact_line(brief: CampaignBrief) -> str:
    return (
        f"{brief.code} · {brief.short_name} · {brief.compensation} · "
        f"{brief.compact_requirement}"
    )


def _region_matches(brief: CampaignBrief, country: str | None) -> bool:
    normalized_regions = {_normalize_region(region) for region in brief.regions}
    if "global" in normalized_regions:
        return True
    normalized_country = _normalize_region(country or "")
    return bool(normalized_country and normalized_country in normalized_regions)


def _platform_matches(brief: CampaignBrief, platform: str | None) -> bool:
    if not brief.platforms:
        return True
    normalized_platform = _normalize_platform(platform or "")
    return bool(
        normalized_platform
        and normalized_platform
        in {_normalize_platform(value) for value in brief.platforms}
    )


def _normalize_platform(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.strip().casefold()).strip()
    aliases = {
        "ig": "instagram",
        "tik tok": "tiktok",
        "yt": "youtube",
        "youtube shorts": "youtube",
    }
    return aliases.get(normalized, normalized)


def _normalize_region(value: str) -> str:
    raw = value.strip().casefold()
    raw_aliases = {
        "美国": "united states",
        "美國": "united states",
    }
    normalized = re.sub(
        r"[^a-z0-9]+",
        " ",
        raw_aliases.get(raw, raw),
    ).strip()
    aliases = {
        "america": "united states",
        "us": "united states",
        "usa": "united states",
        "u s": "united states",
        "u s a": "united states",
        "united states us": "united states",
        "united states of america": "united states",
    }
    return aliases.get(normalized, normalized)
