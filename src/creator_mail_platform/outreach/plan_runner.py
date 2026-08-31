from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func, select, text

from ..core.config import get_settings
from ..core.db import get_engine, session_scope
from ..core.models import ActivityContact
from ..core.utils import extract_send_address, normalize_email_key
from .campaign import get_strategy_route, prepare_campaign, send_prepared_campaign
from .sender import (
    BLOCKED_EMAIL_STATUSES,
    ELIGIBLE_SEND_STATUSES,
    apply_country_holds,
    load_smtp_accounts,
    validate_smtp_account,
)


PlanProgress = Callable[[str, "CampaignJob"], None]
PLAN_LOCK_NAME = "creator_mail.run_plan"


@dataclass(frozen=True)
class CampaignJob:
    batch_no: int
    sender_email: str


def parse_campaign_jobs(value: str) -> list[CampaignJob]:
    """Parse `40=email,41=email` while preserving operator order."""
    jobs: list[CampaignJob] = []
    seen_batches: set[int] = set()
    for raw_item in value.split(","):
        item = raw_item.strip()
        raw_batch, separator, raw_sender = item.partition("=")
        if not separator:
            raise ValueError(f"Invalid job {item!r}; use BATCH=EMAIL")
        try:
            batch_no = int(raw_batch.strip())
        except ValueError as exc:
            raise ValueError(f"Invalid batch number in job {item!r}") from exc
        sender = normalize_email_key(raw_sender)
        if batch_no < 1 or extract_send_address(sender) is None:
            raise ValueError(f"Invalid job {item!r}; use BATCH=EMAIL")
        if batch_no in seen_batches:
            raise ValueError(f"Batch {batch_no} appears more than once in the plan")
        seen_batches.add(batch_no)
        jobs.append(CampaignJob(batch_no=batch_no, sender_email=sender))
    if not jobs:
        raise ValueError("The campaign plan is empty")
    return jobs


def run_campaign_plan(
    jobs: list[CampaignJob],
    *,
    strategy: str,
    workers: int = 8,
    parallel_accounts: int = 3,
    check_mx: bool = True,
    execute: bool = False,
    progress: PlanProgress | None = None,
) -> dict[str, object]:
    """Prepare every job first, then optionally send without interactive prompts."""
    route = get_strategy_route(strategy)
    if not 1 <= workers <= 16:
        raise ValueError("workers must be between 1 and 16")
    if not 1 <= parallel_accounts <= 10:
        raise ValueError("parallel_accounts must be between 1 and 10")
    if not jobs:
        raise ValueError("The campaign plan is empty")

    accounts = load_smtp_accounts()
    for job in jobs:
        if not route.supports_batch(job.batch_no):
            raise ValueError(
                f"{route.name.upper()} uses batch {route.batch_description}; "
                f"batch {job.batch_no} is outside that range"
            )
        account = accounts.get(job.sender_email)
        if account is None:
            raise RuntimeError(
                f"SMTP account {job.sender_email} is not enabled in SMTP_ACCOUNTS_FILE"
            )
        if execute:
            validate_smtp_account(account)
    if execute and not get_settings().smtp_execution_enabled:
        raise RuntimeError(
            "Real sending is disabled. Set SMTP_EXECUTION_ENABLED=true before using --execute."
        )

    with get_engine().connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as lock_connection:
        acquired = bool(
            lock_connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtext(:name))"),
                {"name": PLAN_LOCK_NAME},
            )
        )
        if not acquired:
            raise RuntimeError("Another run-plan process is already active")
        try:
            return _run_locked_plan(
                jobs,
                strategy=route.name,
                workers=workers,
                parallel_accounts=parallel_accounts,
                check_mx=check_mx,
                execute=execute,
                progress=progress,
            )
        finally:
            lock_connection.execute(
                text("SELECT pg_advisory_unlock(hashtext(:name))"),
                {"name": PLAN_LOCK_NAME},
            )


def _run_locked_plan(
    jobs: list[CampaignJob],
    *,
    strategy: str,
    workers: int,
    parallel_accounts: int,
    check_mx: bool,
    execute: bool,
    progress: PlanProgress | None,
) -> dict[str, object]:
    preparations: list[dict[str, object]] = []
    ready_jobs: list[CampaignJob] = []
    completed_jobs: list[CampaignJob] = []

    # Phase one is intentionally sequential: all batches must pass preparation
    # before the first external email is sent.
    for job in jobs:
        apply_country_holds(batch_no=job.batch_no)
        state = _batch_state(job.batch_no)
        if state["total"] == 0:
            raise RuntimeError(f"Batch {job.batch_no} does not exist")
        if state["sendable"] == 0:
            completed_jobs.append(job)
            _notify(progress, "already_complete", job)
            continue
        _notify(progress, "prepare_start", job)
        prepared = prepare_campaign(
            batch_no=job.batch_no,
            sender_email=job.sender_email,
            strategy=strategy,
            workers=workers,
            check_mx=check_mx,
        )
        selected = int(prepared["dry_run"]["selected"])
        skipped = int(prepared["skipped_missing_content"])
        preparations.append(
            {
                "batch_no": job.batch_no,
                "sender_email": job.sender_email,
                "selected": selected,
                "generated_now": int(prepared["generation"]["written"]),
                "ai_failed": int(prepared["skipped_generation_failed"]),
                "skipped_missing_content": skipped,
            }
        )
        if selected > 0:
            ready_jobs.append(job)
        _notify(
            progress,
            "prepare_partial" if skipped > 0 else "prepare_done",
            job,
        )

    if not execute:
        return {
            "execute": False,
            "strategy": strategy,
            "prepared": preparations,
            "already_complete": [_job_dict(job) for job in completed_jobs],
            "send_results": [],
        }

    jobs_by_sender: dict[str, list[CampaignJob]] = defaultdict(list)
    for job in ready_jobs:
        jobs_by_sender[job.sender_email].append(job)

    def send_sender_jobs(item: tuple[str, list[CampaignJob]]) -> dict[str, object]:
        sender, sender_jobs = item
        results: list[dict[str, object]] = []
        for job in sender_jobs:
            _notify(progress, "send_start", job)
            counts = send_prepared_campaign(
                batch_no=job.batch_no,
                sender_email=sender,
            )
            results.append({"batch_no": job.batch_no, **counts})
            _notify(progress, "send_done", job)
        return {"sender_email": sender, "batches": results}

    sender_groups = list(jobs_by_sender.items())
    if sender_groups:
        with ThreadPoolExecutor(
            max_workers=min(parallel_accounts, len(sender_groups)),
            thread_name_prefix="campaign-plan",
        ) as executor:
            send_results = list(executor.map(send_sender_jobs, sender_groups))
    else:
        send_results = []

    return {
        "execute": True,
        "strategy": strategy,
        "prepared": preparations,
        "already_complete": [_job_dict(job) for job in completed_jobs],
        "send_results": send_results,
    }


def _batch_state(batch_no: int) -> dict[str, int]:
    with session_scope() as session:
        total = int(
            session.scalar(
                select(func.count())
                .select_from(ActivityContact)
                .where(ActivityContact.batch_no == batch_no)
            )
            or 0
        )
        sendable = int(
            session.scalar(
                select(func.count())
                .select_from(ActivityContact)
                .where(
                    ActivityContact.batch_no == batch_no,
                    ActivityContact.send_status.in_(ELIGIBLE_SEND_STATUSES),
                    ActivityContact.email_status.not_in(BLOCKED_EMAIL_STATUSES),
                )
            )
            or 0
        )
    return {"total": total, "sendable": sendable}


def _job_dict(job: CampaignJob) -> dict[str, object]:
    return {"batch_no": job.batch_no, "sender_email": job.sender_email}


def _notify(progress: PlanProgress | None, event: str, job: CampaignJob) -> None:
    if progress is not None:
        progress(event, job)
