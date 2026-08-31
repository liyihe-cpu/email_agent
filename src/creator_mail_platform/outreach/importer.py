from __future__ import annotations

from collections.abc import Iterator
import math

from pymilvus import MilvusClient
from sqlalchemy import case, func, null, or_, select, text
from sqlalchemy.dialects.postgresql import insert

from ..core.config import get_settings
from ..core.db import session_scope
from ..core.models import ActivityContact
from ..core.utils import normalize_email_key


SOURCE_FIELDS = [
    "creator_id",
    "platform",
    "handle",
    "followers",
    "email",
    "country",
    "language",
    "primary_category",
    "profile_bio",
    "analysis_note",
]


class CreatorSource:
    """Read-only iterator over the existing Milvus creator collection."""

    def __init__(self) -> None:
        settings = get_settings()
        self.collection = settings.milvus_collection
        self.client = MilvusClient(uri=settings.milvus_uri)

    def iter_creators(self, batch_size: int = 1_000) -> Iterator[list[dict]]:
        iterator = self.client.query_iterator(
            collection_name=self.collection,
            batch_size=batch_size,
            filter="",
            output_fields=SOURCE_FIELDS,
        )
        try:
            while rows := iterator.next():
                yield [dict(row) for row in rows]
        finally:
            iterator.close()


def import_creator_snapshot(
    *,
    data_version: str,
    batch_size: int = 600,
    seed: str = "creator-outreach-v1",
    source_page_size: int = 1_000,
    max_creators: int | None = None,
) -> dict[str, int]:
    """Synchronize every creator that has a creator_id and a nonblank email.

    creator_id is the contact identity. Duplicate email addresses are retained
    as independent creator rows. Existing send assignments and results are not
    reset by a later source sync.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    data_version = data_version.strip()
    if not data_version or data_version.lower() == "unknown":
        raise ValueError("data_version must be explicit and cannot be 'unknown'")
    source = CreatorSource()
    scanned = 0
    candidate_rows = 0
    upserted_rows = 0

    _assert_batch_size_compatible(batch_size)

    for source_rows in source.iter_creators(batch_size=source_page_size):
        # Milvus currently has unique creator_id values. The dict also keeps a
        # single INSERT statement safe if a future page contains a duplicate.
        rows_by_creator_id: dict[str, dict[str, object]] = {}
        for row in source_rows:
            if max_creators is not None and scanned >= max_creators:
                break
            scanned += 1
            creator_id = str(row.get("creator_id") or "").strip()
            email = normalize_email_key(str(row.get("email") or ""))
            if not creator_id or not email:
                continue
            candidate_rows += 1
            language = _optional_text(row.get("language"))
            country = _optional_text(row.get("country"))
            primary_category = _optional_text(row.get("primary_category"))
            profile_bio = _optional_text(row.get("profile_bio"))
            analysis_note = _optional_text(row.get("analysis_note"))
            rows_by_creator_id[creator_id] = {
                "creator_id": creator_id,
                "email": email,
                "batch_no": 0,
                "platform": _optional_text(row.get("platform")),
                "handle": _optional_text(row.get("handle")),
                "follower_count": _optional_int(row.get("followers")),
                "country": country,
                "language": language,
                "primary_category": primary_category,
                "profile_bio": profile_bio,
                "analysis_note": analysis_note,
                "source_data_version": data_version,
            }

        rows = list(rows_by_creator_id.values())
        if rows:
            with session_scope() as session:
                statement = insert(ActivityContact).values(rows)
                excluded = statement.excluded
                pending = ActivityContact.send_status == "pending"
                email_changed = excluded.email.is_distinct_from(ActivityContact.email)
                source_changed = or_(
                    pending & email_changed,
                    excluded.platform.is_distinct_from(ActivityContact.platform),
                    excluded.handle.is_distinct_from(ActivityContact.handle),
                    excluded.follower_count.is_distinct_from(ActivityContact.follower_count),
                    excluded.country.is_distinct_from(ActivityContact.country),
                    excluded.language.is_distinct_from(ActivityContact.language),
                    excluded.primary_category.is_distinct_from(ActivityContact.primary_category),
                    excluded.profile_bio.is_distinct_from(ActivityContact.profile_bio),
                    excluded.analysis_note.is_distinct_from(ActivityContact.analysis_note),
                )
                needs_baseline = ActivityContact.source_data_version == "unknown"
                session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["creator_id"],
                        set_={
                            "email": case(
                                (pending, excluded.email),
                                else_=ActivityContact.email,
                            ),
                            "platform": excluded.platform,
                            "handle": excluded.handle,
                            "follower_count": excluded.follower_count,
                            "country": excluded.country,
                            "language": excluded.language,
                            "primary_category": excluded.primary_category,
                            "profile_bio": excluded.profile_bio,
                            "analysis_note": excluded.analysis_note,
                            "source_data_version": case(
                                (source_changed | needs_baseline, excluded.source_data_version),
                                else_=ActivityContact.source_data_version,
                            ),
                            # 待发达人换邮箱后，需要重新验证新地址。
                            "email_status": case(
                                (pending & email_changed, "unknown"),
                                else_=ActivityContact.email_status,
                            ),
                            "status_reason": case(
                                (pending & email_changed, null()),
                                else_=ActivityContact.status_reason,
                            ),
                        },
                    )
                )
                upserted_rows += len(rows)
        if max_creators is not None and scanned >= max_creators:
            break

    with session_scope() as session:
        new_count = int(
            session.scalar(
                select(func.count()).select_from(ActivityContact).where(ActivityContact.batch_no == 0)
            )
            or 0
        )
        session.execute(
            text(
                f"""
                WITH existing AS (
                    SELECT count(*)::bigint AS row_count
                    FROM {ActivityContact.__table__.fullname}
                    WHERE batch_no > 0
                ), new_ranked AS (
                    SELECT creator_id,
                           row_number() OVER (
                               ORDER BY md5(:seed || creator_id), creator_id
                           ) AS row_no
                    FROM {ActivityContact.__table__.fullname}
                    WHERE batch_no = 0
                )
                UPDATE {ActivityContact.__table__.fullname} AS contacts
                SET batch_no = ((existing.row_count + new_ranked.row_no - 1) / :batch_size)::integer + 1
                FROM existing, new_ranked
                WHERE contacts.creator_id = new_ranked.creator_id
                """
            ),
            {"seed": seed, "batch_size": batch_size},
        )
        total = int(
            session.execute(
                text(f"SELECT count(*) FROM {ActivityContact.__table__.fullname}")
            ).scalar_one()
        )

    return {
        "scanned": scanned,
        "candidate_rows": candidate_rows,
        "upserted_rows": upserted_rows,
        "newly_batched": new_count,
        "total": total,
        "batch_count": math.ceil(total / batch_size) if total else 0,
    }


def _assert_batch_size_compatible(batch_size: int) -> None:
    """Prevent a later sync from silently changing the established batch size."""
    with session_scope() as session:
        rows = list(
            session.execute(
                select(ActivityContact.batch_no, func.count())
                .where(ActivityContact.batch_no > 0)
                .group_by(ActivityContact.batch_no)
                .order_by(ActivityContact.batch_no)
            )
        )
    if not rows:
        return
    for batch_no, count in rows[:-1]:
        if count != batch_size:
            raise RuntimeError(
                f"Existing batch {batch_no} contains {count} rows. "
                f"Continue using batch_size={count}, not {batch_size}."
            )
    last_batch_no, last_count = rows[-1]
    if last_count > batch_size:
        raise RuntimeError(
            f"Existing final batch {last_batch_no} contains {last_count} rows, "
            f"which exceeds batch_size={batch_size}."
        )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None
