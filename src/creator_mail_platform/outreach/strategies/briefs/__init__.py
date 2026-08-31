from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


BRIEFS_FILE = Path(__file__).with_name("briefs.json")


@dataclass(frozen=True)
class CampaignBrief:
    code: str
    name: str
    categories: tuple[str, ...]
    regions: tuple[str, ...]
    platforms: tuple[str, ...]
    payload: dict[str, object]

    @property
    def short_name(self) -> str:
        return str(self.payload.get("short_name") or self.name).strip()

    @property
    def primary_category(self) -> str:
        return str(self.payload.get("primary_category") or "Other").strip()

    @property
    def product_name(self) -> str:
        return str(self.payload.get("product_name") or self.short_name).strip()

    @property
    def deliverables(self) -> str:
        return str(self.payload.get("deliverables") or "To be confirmed").strip()

    @property
    def compensation(self) -> str:
        return str(self.payload.get("compensation") or "To be confirmed").strip()

    @property
    def timeline(self) -> str:
        return str(self.payload.get("timeline") or "To be confirmed").strip()

    @property
    def usage_rights(self) -> str:
        return str(self.payload.get("usage_rights") or "To be confirmed").strip()

    @property
    def compact_requirement(self) -> str:
        return str(
            self.payload.get("compact_requirement") or self.deliverables
        ).strip()

    @property
    def subjects(self) -> tuple[str, ...]:
        configured = self.payload.get("subjects")
        if isinstance(configured, list):
            return tuple(str(value).strip() for value in configured if str(value).strip())
        return ()

    @property
    def is_fallback(self) -> bool:
        return bool(self.payload.get("is_fallback", False))


def load_active_briefs(path: Path = BRIEFS_FILE) -> list[CampaignBrief]:
    document = json.loads(path.read_text(encoding="utf-8"))
    raw_briefs = document.get("briefs", []) if isinstance(document, dict) else document
    if not isinstance(raw_briefs, list):
        raise RuntimeError("briefs.json must be a list or contain a briefs list")

    briefs: list[CampaignBrief] = []
    for raw in raw_briefs:
        if not isinstance(raw, dict) or raw.get("enabled", True) is False:
            continue
        code = str(raw.get("brief_code") or raw.get("code") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not code or not name:
            raise RuntimeError("Every enabled brief needs code and name")
        if any(brief.code == code for brief in briefs):
            raise RuntimeError(f"Duplicate enabled brief_code: {code}")
        configured_subjects = raw.get("subjects")
        if configured_subjects is not None and not isinstance(configured_subjects, list):
            raise RuntimeError(f"Enabled brief {code} subjects must be a list")
        subjects = [str(value).strip() for value in (configured_subjects or []) if str(value).strip()]
        if not subjects:
            raise RuntimeError(f"Enabled brief {code} needs at least one subject")
        if len(subjects) > 2:
            raise RuntimeError(f"Enabled brief {code} supports at most two subjects")
        briefs.append(
            CampaignBrief(
                code=code,
                name=name,
                categories=tuple(str(value) for value in (raw.get("categories") or [])),
                regions=tuple(str(value) for value in (raw.get("regions") or [])),
                platforms=tuple(str(value) for value in (raw.get("platforms") or [])),
                payload=dict(raw),
            )
        )
    fallback_count = sum(bool(brief.payload.get("is_fallback")) for brief in briefs)
    if briefs and fallback_count != 1:
        raise RuntimeError("Exactly one enabled brief must set is_fallback=true")
    return briefs
