from __future__ import annotations

from collections.abc import Callable
import re
import smtplib
import time

from sqlalchemy import and_, func, select
from sqlalchemy.exc import DBAPIError, OperationalError

from ..core.db import session_scope
from ..core.models import ActivityContact
from .plan_runner import CampaignJob, run_campaign_plan
from .sender import BLOCKED_EMAIL_STATUSES, ELIGIBLE_SEND_STATUSES, load_smtp_accounts


QueueProgress = Callable[[str, dict[str, object]], None]
COOPERATION_ACCOUNT_PATTERN = re.compile(r"^cooperation(\d+)@coojoy\.cn$")
S1_QUEUE_MIN_BATCH = 76
DEFAULT_S1_BATCH_FROM = 106
DEFAULT_S1_BATCH_TO = 135


def build_s1_queue_jobs(
    *,
    batch_from: int,
    batch_to: int,
) -> tuple[list[CampaignJob], list[str]]:
    if batch_from < S1_QUEUE_MIN_BATCH or batch_to < batch_from:
        raise ValueError(
            f"The resumable S1 queue requires batch {S1_QUEUE_MIN_BATCH} or later"
        )

    accounts = [
        email
        for email in load_smtp_accounts()
        if COOPERATION_ACCOUNT_PATTERN.fullmatch(email)
    ]
    accounts.sort(key=_account_number)
    if not accounts:
        raise RuntimeError("No enabled cooperationXX@coojoy.cn SMTP account is configured")

    jobs = [
        CampaignJob(
            batch_no=batch_no,
            sender_email=accounts[(batch_no - batch_from) % len(accounts)],
        )
        for batch_no in range(batch_from, batch_to + 1)
    ]
    return jobs, accounts


def run_resumable_s1_queue(
    *,
    batch_from: int = DEFAULT_S1_BATCH_FROM,
    batch_to: int = DEFAULT_S1_BATCH_TO,
    workers: int = 8,
    parallel_accounts: int = 10,
    execute: bool = False,
    retry_seconds: int = 300,
    max_retry_seconds: int = 3_600,
    progress: QueueProgress | None = None,
) -> dict[str, object]:
    """Prepare and resume S1 waves until no safely retryable email remains."""
    if retry_seconds < 10:
        raise ValueError("retry_seconds must be at least 10")
    if max_retry_seconds < retry_seconds:
        raise ValueError("max_retry_seconds must be at least retry_seconds")

    jobs, accounts = build_s1_queue_jobs(
        batch_from=batch_from,
        batch_to=batch_to,
    )
    wave_size = min(len(accounts), parallel_accounts)
    waves = [jobs[index : index + wave_size] for index in range(0, len(jobs), wave_size)]
    wave_results: list[dict[str, object]] = []

    for wave_number, wave in enumerate(waves, start=1):
        delay = retry_seconds
        previous_sendable: int | None = None
        while True:
            _notify(
                progress,
                "wave_start",
                {
                    "wave": wave_number,
                    "wave_count": len(waves),
                    "batch_from": wave[0].batch_no,
                    "batch_to": wave[-1].batch_no,
                },
            )
            try:
                def plan_progress(event: str, job: CampaignJob) -> None:
                    _notify(
                        progress,
                        "plan_progress",
                        {
                            "event": event,
                            "batch_no": job.batch_no,
                            "sender_email": job.sender_email,
                        },
                    )

                result = run_campaign_plan(
                    wave,
                    strategy="s1",
                    workers=workers,
                    parallel_accounts=min(parallel_accounts, len(wave)),
                    check_mx=True,
                    execute=execute,
                    progress=plan_progress,
                )
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                if not _is_transient(exc):
                    raise
                _notify(
                    progress,
                    "waiting",
                    {
                        "seconds": delay,
                        "reason": f"{type(exc).__name__}: {exc}",
                    },
                )
                time.sleep(delay)
                delay = min(delay * 2, max_retry_seconds)
                continue

            state = collect_queue_state(wave)
            wave_results.append(
                {
                    "wave": wave_number,
                    "batch_from": wave[0].batch_no,
                    "batch_to": wave[-1].batch_no,
                    "plan": result,
                    "state": state,
                }
            )
            _notify(progress, "wave_state", {"wave": wave_number, **state})

            if not execute or int(state["sendable"]) == 0:
                break

            current_sendable = int(state["sendable"])
            if previous_sendable is None or current_sendable < previous_sendable:
                delay = retry_seconds
            else:
                delay = min(delay * 2, max_retry_seconds)
            previous_sendable = current_sendable
            _notify(
                progress,
                "waiting",
                {
                    "seconds": delay,
                    "reason": f"{current_sendable} safely retryable emails remain",
                },
            )
            time.sleep(delay)

    final_state = collect_queue_state(jobs)
    status = "dry_run"
    if execute:
        status = "needs_review" if int(final_state["delivery_unknown"]) else "complete"
    return {
        "status": status,
        "execute": execute,
        "batch_from": batch_from,
        "batch_to": batch_to,
        "accounts": accounts,
        "waves": len(waves),
        "final_state": final_state,
        "wave_results": wave_results,
    }


def collect_queue_state(jobs: list[CampaignJob]) -> dict[str, int]:
    batch_numbers = [job.batch_no for job in jobs]
    sendable_condition = and_(
        ActivityContact.send_status.in_(ELIGIBLE_SEND_STATUSES),
        ActivityContact.email_status.not_in(BLOCKED_EMAIL_STATUSES),
    )
    with session_scope() as session:
        row = session.execute(
            select(
                func.count().label("total"),
                func.count().filter(sendable_condition).label("sendable"),
                func.count()
                .filter(ActivityContact.send_status == "smtp_accepted")
                .label("smtp_accepted"),
                func.count()
                .filter(ActivityContact.send_status == "temporary_failed")
                .label("temporary_failed"),
                func.count()
                .filter(ActivityContact.send_status == "pending")
                .label("pending"),
                func.count()
                .filter(
                    ActivityContact.send_status == "sending"
                )
                .label("delivery_unknown"),
            ).where(ActivityContact.batch_no.in_(batch_numbers))
        ).one()._mapping
    return {key: int(value or 0) for key, value in row.items()}


def _account_number(email: str) -> int:
    match = COOPERATION_ACCOUNT_PATTERN.fullmatch(email)
    if match is None:
        raise ValueError(f"Unsupported queue account: {email}")
    return int(match.group(1))


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (OperationalError, DBAPIError, OSError, TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, smtplib.SMTPException):
        return True
    message = str(exc).casefold()
    return any(
        marker in message
        for marker in (
            "another run-plan process is already active",
            "connection closed",
            "connection reset",
            "connection timed out",
            "server disconnected",
            "temporarily unavailable",
        )
    )


def _notify(
    progress: QueueProgress | None,
    event: str,
    payload: dict[str, object],
) -> None:
    if progress is not None:
        progress(event, payload)
