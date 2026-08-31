from __future__ import annotations

from sqlalchemy import and_, case, func, or_, select

from ..core.db import session_scope
from ..core.models import ActivityContact


STRATEGY_FILTERS = {
    "s1": or_(
        ActivityContact.batch_no.between(1, 25),
        ActivityContact.batch_no >= 76,
    ),
    "s2": ActivityContact.batch_no.between(26, 50),
    "s3": ActivityContact.batch_no.between(51, 75),
}

STARTED_SEND_STATUSES = (
    "sending",
    "smtp_accepted",
    "temporary_failed",
    "hard_failed",
)


def collect_stats(
    batch_no: int | None = None,
    *,
    batch_from: int | None = None,
    batch_to: int | None = None,
) -> dict[str, object]:
    if batch_no is not None and (batch_from is not None or batch_to is not None):
        raise ValueError("Use either --batch or --batch-from/--batch-to")
    if (batch_from is None) != (batch_to is None):
        raise ValueError("--batch-from and --batch-to must be used together")
    if batch_from is not None and batch_to is not None and batch_from > batch_to:
        raise ValueError("--batch-from cannot be greater than --batch-to")

    with session_scope() as session:
        if batch_no is not None:
            contact_filter = ActivityContact.batch_no == batch_no
            scope = f"batch {batch_no}"
        elif batch_from is not None and batch_to is not None:
            contact_filter = ActivityContact.batch_no.between(batch_from, batch_to)
            scope = f"batch {batch_from}-{batch_to}"
        else:
            contact_filter = None
            scope = "全部达人"

        send_statement = select(ActivityContact.send_status, func.count()).group_by(ActivityContact.send_status)
        email_statement = select(ActivityContact.email_status, func.count()).group_by(ActivityContact.email_status)
        combination_statement = (
            select(
                ActivityContact.email_status,
                ActivityContact.send_status,
                func.count().label("count"),
            )
            .group_by(ActivityContact.email_status, ActivityContact.send_status)
            .order_by(func.count().desc())
        )
        version_statement = select(ActivityContact.source_data_version, func.count()).group_by(
            ActivityContact.source_data_version
        )
        reply_statement = (
            select(ActivityContact.reply_type, func.count())
            .where(ActivityContact.reply_type.is_not(None))
            .group_by(ActivityContact.reply_type)
        )
        reply_total_statement = select(func.coalesce(func.sum(ActivityContact.reply_count), 0))
        strategy_value = case(
            (
                or_(
                    ActivityContact.batch_no.between(1, 25),
                    ActivityContact.batch_no >= 76,
                ),
                1,
            ),
            (ActivityContact.batch_no.between(26, 50), 2),
            (ActivityContact.batch_no.between(51, 75), 3),
            else_=None,
        )
        strategy_statement = (
            select(
                strategy_value.label("strategy_no"),
                ActivityContact.send_status,
                func.count(),
            )
            .where(ActivityContact.batch_no >= 1)
            .group_by(strategy_value, ActivityContact.send_status)
            .order_by(strategy_value, ActivityContact.send_status)
        )
        sender_statement = (
            select(
                func.lower(ActivityContact.sender_email),
                ActivityContact.send_status,
                func.count(),
            )
            .where(ActivityContact.sender_email.is_not(None))
            .group_by(func.lower(ActivityContact.sender_email), ActivityContact.send_status)
            .order_by(func.lower(ActivityContact.sender_email), ActivityContact.send_status)
        )
        if contact_filter is not None:
            send_statement = send_statement.where(contact_filter)
            email_statement = email_statement.where(contact_filter)
            combination_statement = combination_statement.where(contact_filter)
            version_statement = version_statement.where(contact_filter)
            reply_statement = reply_statement.where(contact_filter)
            reply_total_statement = reply_total_statement.where(contact_filter)
            strategy_statement = strategy_statement.where(contact_filter)
            sender_statement = sender_statement.where(contact_filter)

        send_statuses = {str(status): int(count) for status, count in session.execute(send_statement)}
        email_statuses = {str(status): int(count) for status, count in session.execute(email_statement)}
        status_combinations = [
            {
                "email_status": str(email_status),
                "send_status": str(send_status),
                "count": int(count),
            }
            for email_status, send_status, count in session.execute(combination_statement)
        ]
        delivery_summary = _summarize_delivery(status_combinations)
        source_data_versions = {
            str(version): int(count) for version, count in session.execute(version_statement)
        }
        reply_types = {str(status): int(count) for status, count in session.execute(reply_statement)}
        reply_messages_total = int(session.scalar(reply_total_statement) or 0)
        engagement_summary = _collect_engagement_summary(
            session,
            contact_filter=contact_filter,
            batch_from=batch_no if batch_no is not None else batch_from,
            batch_to=batch_no if batch_no is not None else batch_to,
        )
        strategy_summaries = {}
        if contact_filter is None:
            strategy_summaries = {
                strategy: _collect_engagement_summary(
                    session,
                    contact_filter=strategy_filter,
                )
                for strategy, strategy_filter in STRATEGY_FILTERS.items()
            }
        strategy_progress: dict[str, dict[str, int]] = {}
        for strategy, status, count in session.execute(strategy_statement):
            strategy_progress.setdefault(str(int(strategy)), {})[str(status)] = int(count)
        sender_progress: dict[str, dict[str, int]] = {}
        for sender, status, count in session.execute(sender_statement):
            sender_progress.setdefault(str(sender), {})[str(status)] = int(count)
        return {
            "statistics_scope": "cooperation_campaigns_only",
            "batch_no": batch_no,
            "batch_from": batch_from,
            "batch_to": batch_to,
            "scope": scope,
            "delivery_summary": delivery_summary,
            "status_combinations": status_combinations,
            "send_statuses": send_statuses,
            "email_statuses": email_statuses,
            "source_data_versions": source_data_versions,
            "reply_types": reply_types,
            "reply_messages_total": reply_messages_total,
            "engagement_summary": engagement_summary,
            "strategy_summaries": strategy_summaries,
            "strategy_progress": strategy_progress,
            "sender_progress": sender_progress,
        }


def _collect_engagement_summary(
    session: object,
    *,
    contact_filter: object | None,
    batch_from: int | None = None,
    batch_to: int | None = None,
) -> dict[str, object]:
    sent = ActivityContact.send_status == "smtp_accepted"
    any_reply = and_(sent, ActivityContact.reply_count > 0)
    human_reply = and_(sent, ActivityContact.reply_type == "human_reply")
    auto_reply = and_(sent, ActivityContact.reply_type == "auto_reply")
    hard_bounce = and_(sent, ActivityContact.reply_type == "hard_bounce")
    soft_bounce = and_(sent, ActivityContact.reply_type == "soft_bounce")
    unsubscribe = and_(sent, ActivityContact.reply_type == "unsubscribe")
    form_submitted = and_(sent, ActivityContact.form_submitted_at.is_not(None))

    response = ActivityContact.form_response_json
    optional_completed = func.coalesce(
        response["optional_profile_completed"].as_boolean(),
        False,
    )
    category_bonus = func.coalesce(
        response["reward_breakdown_usd"]["category_preferences"].as_integer(),
        0,
    )
    contact_bonus = func.coalesce(
        response["reward_breakdown_usd"]["additional_contact"].as_integer(),
        0,
    )
    category_completed = category_bonus > 0
    contact_completed = contact_bonus > 0

    statement = select(
        func.count().label("total_contacts"),
        func.count().filter(sent).label("smtp_accepted"),
        func.count()
        .filter(and_(sent, ActivityContact.email_status == "possibly_usable"))
        .label("accepted_possibly_usable"),
        func.count()
        .filter(and_(sent, ActivityContact.email_status == "usable"))
        .label("accepted_usable"),
        func.count().filter(any_reply).label("reply_contacts"),
        func.coalesce(
            func.sum(case((sent, ActivityContact.reply_count), else_=0)),
            0,
        ).label("reply_messages"),
        func.count().filter(human_reply).label("human_reply_contacts"),
        func.count().filter(auto_reply).label("auto_reply_contacts"),
        func.count().filter(hard_bounce).label("hard_bounce_contacts"),
        func.count().filter(soft_bounce).label("soft_bounce_contacts"),
        func.count().filter(unsubscribe).label("unsubscribe_contacts"),
        func.count().filter(form_submitted).label("form_submitted"),
        func.count()
        .filter(and_(form_submitted, optional_completed.is_(True)))
        .label("optional_completed"),
        func.count()
        .filter(and_(form_submitted, category_completed))
        .label("category_selected"),
        func.count()
        .filter(and_(form_submitted, contact_completed))
        .label("added_contact"),
        func.count()
        .filter(
            and_(
                form_submitted,
                optional_completed.is_(True),
                contact_bonus == 0,
            )
        )
        .label("category_only"),
        func.count()
        .filter(and_(form_submitted, contact_bonus == 0))
        .label("no_contact"),
        func.count()
        .filter(and_(form_submitted, optional_completed.is_(False)))
        .label("joined_only"),
        func.count()
        .filter(and_(any_reply, ActivityContact.form_submitted_at.is_not(None)))
        .label("any_reply_and_form"),
        func.count()
        .filter(and_(human_reply, ActivityContact.form_submitted_at.is_not(None)))
        .label("human_reply_and_form"),
        func.count()
        .filter(or_(human_reply, form_submitted))
        .label("human_reply_or_form"),
    )
    if contact_filter is not None:
        statement = statement.where(contact_filter)

    row = session.execute(statement).one()._mapping
    values = {key: int(value or 0) for key, value in row.items()}
    started = _collect_started_batch_progress(
        session,
        contact_filter=contact_filter,
    )
    accepted = values["smtp_accepted"]
    form_total = values["form_submitted"]
    human_total = values["human_reply_contacts"]
    accepted_other = max(
        accepted
        - values["accepted_possibly_usable"]
        - values["accepted_usable"],
        0,
    )

    return {
        "batch_from": batch_from,
        "batch_to": batch_to,
        "delivery": {
            "total_contacts": values["total_contacts"],
            "smtp_accepted": accepted,
            **started,
            "accepted_possibly_usable": values["accepted_possibly_usable"],
            "accepted_usable": values["accepted_usable"],
            "accepted_other_email_status": accepted_other,
        },
        "email_engagement": {
            "reply_messages_total": values["reply_messages"],
            "reply_contacts": values["reply_contacts"],
            "reply_contact_rate_pct": _percentage(values["reply_contacts"], accepted),
            "human_reply_contacts": human_total,
            "human_reply_rate_pct": _percentage(human_total, accepted),
            "auto_reply_contacts": values["auto_reply_contacts"],
            "hard_bounce_contacts": values["hard_bounce_contacts"],
            "soft_bounce_contacts": values["soft_bounce_contacts"],
            "unsubscribe_contacts": values["unsubscribe_contacts"],
            "classification_basis": "latest_reply_type_per_creator",
        },
        "form_engagement": {
            "total_form_submitted": form_total,
            "form_submission_rate_pct": _percentage(form_total, accepted),
            "completed_optional": values["optional_completed"],
            "completed_optional_rate_pct": _percentage(values["optional_completed"], accepted),
            "category_selected_count": values["category_selected"],
            "category_selected_rate_pct": _percentage(values["category_selected"], accepted),
            "added_contact_count": values["added_contact"],
            "added_contact_rate_pct": _percentage(values["added_contact"], accepted),
            "category_only_count": values["category_only"],
            "category_only_rate_pct": _percentage(values["category_only"], accepted),
            "no_contact_count": values["no_contact"],
            "no_contact_rate_pct": _percentage(values["no_contact"], accepted),
            "joined_only_count": values["joined_only"],
            "optional_completion_of_forms_pct": _percentage(
                values["optional_completed"], form_total
            ),
            "contact_completion_of_forms_pct": _percentage(
                values["added_contact"], form_total
            ),
        },
        "cross_channel": {
            "any_reply_and_form_count": values["any_reply_and_form"],
            "human_reply_and_form_count": values["human_reply_and_form"],
            "human_reply_or_form_count": values["human_reply_or_form"],
            "human_reply_and_form_rate_of_sent_pct": _percentage(
                values["human_reply_and_form"], accepted
            ),
            "form_users_also_human_replied_pct": _percentage(
                values["human_reply_and_form"], form_total
            ),
            "human_repliers_also_submitted_form_pct": _percentage(
                values["human_reply_and_form"], human_total
            ),
        },
    }


def _collect_started_batch_progress(
    session: object,
    *,
    contact_filter: object | None,
) -> dict[str, object]:
    started_batch_statement = (
        select(ActivityContact.batch_no)
        .where(ActivityContact.send_status.in_(STARTED_SEND_STATUSES))
        .distinct()
        .order_by(ActivityContact.batch_no)
    )
    if contact_filter is not None:
        started_batch_statement = started_batch_statement.where(contact_filter)
    started_batches = list(session.scalars(started_batch_statement))

    if not started_batches:
        return {
            "sent_batch_count": 0,
            "first_sent_batch": None,
            "last_sent_batch": None,
            "started_batch_contacts": 0,
            "smtp_accepted_of_started_batches_pct": 0.0,
            "precheck_invalid": 0,
            "precheck_invalid_rate_pct": 0.0,
            "precheck_passed": 0,
            "smtp_accepted_of_precheck_passed_pct": 0.0,
            "pending_sendable": 0,
            "temporary_failed": 0,
            "hard_failed": 0,
            "sending": 0,
            "on_hold": 0,
        }

    started_scope = ActivityContact.batch_no.in_(started_batches)
    statement = select(
        func.count().label("started_batch_contacts"),
        func.count()
        .filter(ActivityContact.send_status == "smtp_accepted")
        .label("smtp_accepted"),
        func.count()
        .filter(
            and_(
                ActivityContact.send_status == "pending",
                ActivityContact.email_status == "invalid",
            )
        )
        .label("precheck_invalid"),
        func.count()
        .filter(
            and_(
                ActivityContact.send_status == "pending",
                ActivityContact.email_status.not_in({"invalid", "suppressed"}),
            )
        )
        .label("pending_sendable"),
        func.count()
        .filter(ActivityContact.send_status == "temporary_failed")
        .label("temporary_failed"),
        func.count()
        .filter(ActivityContact.send_status == "hard_failed")
        .label("hard_failed"),
        func.count()
        .filter(ActivityContact.send_status == "sending")
        .label("sending"),
        func.count()
        .filter(ActivityContact.send_status == "on_hold")
        .label("on_hold"),
    ).where(started_scope)
    if contact_filter is not None:
        statement = statement.where(contact_filter)
    row = session.execute(statement).one()._mapping
    values = {key: int(value or 0) for key, value in row.items()}
    started_total = values["started_batch_contacts"]
    accepted = values["smtp_accepted"]
    precheck_passed = max(started_total - values["precheck_invalid"], 0)

    return {
        "sent_batch_count": len(started_batches),
        "first_sent_batch": int(started_batches[0]),
        "last_sent_batch": int(started_batches[-1]),
        "started_batch_contacts": started_total,
        "smtp_accepted_of_started_batches_pct": _percentage(
            accepted,
            started_total,
        ),
        "precheck_invalid": values["precheck_invalid"],
        "precheck_invalid_rate_pct": _percentage(
            values["precheck_invalid"],
            started_total,
        ),
        "precheck_passed": precheck_passed,
        "smtp_accepted_of_precheck_passed_pct": _percentage(
            accepted,
            precheck_passed,
        ),
        "pending_sendable": values["pending_sendable"],
        "temporary_failed": values["temporary_failed"],
        "hard_failed": values["hard_failed"],
        "sending": values["sending"],
        "on_hold": values["on_hold"],
    }


def _percentage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator * 100 / denominator, 2)


def _summarize_delivery(
    combinations: list[dict[str, object]],
) -> dict[str, int]:
    def count_where(*, email: str | None = None, send: str | None = None) -> int:
        return sum(
            int(row["count"])
            for row in combinations
            if (email is None or row["email_status"] == email)
            and (send is None or row["send_status"] == send)
        )

    pending_sendable = sum(
        int(row["count"])
        for row in combinations
        if row["send_status"] == "pending"
        and row["email_status"] not in {"invalid", "suppressed"}
    )
    return {
        "total": sum(int(row["count"]) for row in combinations),
        "smtp_accepted": count_where(send="smtp_accepted"),
        "invalid_total": count_where(email="invalid"),
        "precheck_invalid": sum(
            int(row["count"])
            for row in combinations
            if row["email_status"] == "invalid"
            and row["send_status"] == "pending"
        ),
        "hard_failed": count_where(send="hard_failed"),
        "temporary_failed": count_where(send="temporary_failed"),
        "pending_sendable": pending_sendable,
        "sending": count_where(send="sending"),
        "on_hold": count_where(send="on_hold"),
    }
