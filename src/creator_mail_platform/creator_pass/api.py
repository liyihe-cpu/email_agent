from __future__ import annotations

from collections.abc import Iterator
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from ..core.config import PROJECT_ROOT, get_settings
from .service import (
    FormTokenError,
    join_creator_pass,
    update_creator_preferences,
    verify_form_token,
)
from ..core.db import get_session_factory
from ..core.models import ActivityContact
from ..core.taxonomy import (
    COLLABORATION_CATEGORIES,
    COLLABORATION_TAXONOMY,
    CONTACT_METHODS,
)


FRONTEND_DIR = PROJECT_ROOT / "frontend" / "creator_pass"
PAGE_PATH = FRONTEND_DIR / "index.html"
LOGO_PATHS = (
    FRONTEND_DIR / "assets" / "coojoy-logo.png",
    FRONTEND_DIR / "assets" / "coojoy-logo.jpg",
    FRONTEND_DIR / "assets" / "coojoy-logo.jpeg",
)

app = FastAPI(
    title="COOJOY Creator Pass",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class TokenRequest(BaseModel):
    token: str = Field(min_length=10, max_length=4096)


class PreferencesRequest(TokenRequest):
    collaboration_categories: list[str] = Field(default_factory=list, max_length=15)
    collaboration_details: dict[str, list[str]] = Field(default_factory=dict)
    other_category: str = Field(default="", max_length=200)
    additional_contact_method: str = Field(default="", max_length=32)
    additional_contact_value: str = Field(default="", max_length=500)
    contact_consent: bool = False

    @field_validator("collaboration_categories")
    @classmethod
    def validate_categories(cls, value: list[str]) -> list[str]:
        unique = list(dict.fromkeys(value))
        invalid = set(unique).difference(COLLABORATION_CATEGORIES)
        if invalid:
            raise ValueError("unsupported collaboration category")
        return unique

    @field_validator("other_category", "additional_contact_method", "additional_contact_value")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_details(self) -> "PreferencesRequest":
        selected = set(self.collaboration_categories)
        if set(self.collaboration_details).difference(selected):
            raise ValueError("detail category must also be selected")
        cleaned: dict[str, list[str]] = {}
        for category, details in self.collaboration_details.items():
            unique = list(dict.fromkeys(details))
            allowed = set(COLLABORATION_TAXONOMY[category])
            if set(unique).difference(allowed):
                raise ValueError("unsupported collaboration detail")
            cleaned[category] = unique
        self.collaboration_details = cleaned
        if self.other_category and "Other" not in selected:
            raise ValueError("Other must be selected before entering another category")
        if self.additional_contact_method and self.additional_contact_method not in CONTACT_METHODS:
            raise ValueError("unsupported contact method")
        if bool(self.additional_contact_method) != bool(self.additional_contact_value):
            raise ValueError("contact method and value must be provided together")
        return self


def get_form_session() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@app.middleware("http")
async def add_security_headers(request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "img-src 'self' data:; frame-ancestors 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/creator/assets/coojoy-logo", include_in_schema=False)
def coojoy_logo() -> FileResponse:
    for path in LOGO_PATHS:
        if path.exists():
            media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            return FileResponse(path, media_type=media_type)
    raise HTTPException(status_code=404)


@app.get("/creator", include_in_schema=False)
def creator_pass_redirect() -> RedirectResponse:
    return RedirectResponse(url="/creator/", status_code=308)


@app.get("/creator/", response_class=HTMLResponse)
def creator_pass_page() -> HTMLResponse:
    return HTMLResponse(PAGE_PATH.read_text(encoding="utf-8"))


@app.get("/creator/preview", response_class=HTMLResponse)
def creator_pass_preview() -> HTMLResponse:
    """Interactive appearance preview; its browser code never calls write APIs."""
    return HTMLResponse(PAGE_PATH.read_text(encoding="utf-8"))


@app.get("/creator/api/options")
def creator_pass_options() -> dict[str, object]:
    return _form_options()


@app.get("/creator/api/context")
def creator_pass_context(
    t: str = Query(min_length=10, max_length=4096),
    session: Session = Depends(get_form_session),
) -> dict[str, object]:
    contact = _load_contact(t, session)
    response = dict(contact.form_response_json or {})
    return {
        "display_name": _display_name(contact.handle),
        "joined": response.get("joined") is True,
        "collaboration_categories": response.get("collaboration_categories", []),
        "collaboration_details": response.get("collaboration_details", {}),
        "other_category": response.get("other_category", ""),
        "additional_contact_method": response.get("additional_contact_method", ""),
        "additional_contact_value": response.get("additional_contact_value", ""),
        "contact_consent": response.get("contact_consent", False),
        **_form_options(),
        "reward_breakdown_usd": response.get("reward_breakdown_usd", {}),
        "reward_total_usd": response.get("reward_total_usd", 0),
    }


@app.post("/creator/api/join")
def join(
    request: TokenRequest,
    session: Session = Depends(get_form_session),
) -> dict[str, object]:
    contact = _load_contact(request.token, session, lock=True)
    response = join_creator_pass(contact)
    return {
        "joined": True,
        "joined_at": response["joined_at"],
        "reward_status": contact.reward_status,
        "welcome_bonus_usd": response["welcome_bonus_usd"],
    }


@app.post("/creator/api/preferences")
def save_preferences(
    request: PreferencesRequest,
    session: Session = Depends(get_form_session),
) -> dict[str, object]:
    if request.additional_contact_value and not request.contact_consent:
        raise HTTPException(
            status_code=422,
            detail="Please authorize us to use the additional contact information.",
        )
    contact = _load_contact(request.token, session, lock=True)
    try:
        response = update_creator_preferences(
            contact,
            collaboration_categories=request.collaboration_categories,
            collaboration_details=request.collaboration_details,
            other_category=request.other_category,
            additional_contact_method=request.additional_contact_method,
            additional_contact_value=request.additional_contact_value,
            contact_consent=request.contact_consent,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "saved": True,
        "optional_profile_completed": response["optional_profile_completed"],
        "reward_breakdown_usd": response["reward_breakdown_usd"],
        "reward_total_usd": response["reward_total_usd"],
    }


def _load_contact(token: str, session: Session, *, lock: bool = False) -> ActivityContact:
    settings = get_settings()
    try:
        creator_id = verify_form_token(
            token,
            secret=settings.form_token_secret.get_secret_value(),
        )
    except (FormTokenError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="This invitation link is invalid or expired.") from exc
    contact = session.get(ActivityContact, creator_id, with_for_update=lock)
    if contact is None:
        raise HTTPException(status_code=404, detail="This invitation link is invalid or expired.")
    return contact


def _display_name(handle: str | None) -> str:
    clean = (handle or "").strip().lstrip("@")
    return f"@{clean}" if clean else "Creator"


def _form_options() -> dict[str, object]:
    return {
        "categories": list(COLLABORATION_CATEGORIES),
        "category_details": {
            category: list(details) for category, details in COLLABORATION_TAXONOMY.items()
        },
        "contact_methods": list(CONTACT_METHODS),
    }
