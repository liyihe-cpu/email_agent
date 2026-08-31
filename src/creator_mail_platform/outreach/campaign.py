from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .scheduler import assign_batch_to_sender
from .sender import apply_country_holds, send_batch
from .strategies.s1_direct_pitch import (
    S1_CONTINUATION_FIRST_BATCH,
    S1_FIRST_BATCH,
    S1_LAST_BATCH,
    generate_s1_messages,
)
from .strategies.s2_creator_pass import (
    S2_FIRST_BATCH,
    S2_LAST_BATCH,
    generate_s2_messages,
)
from .strategies.s3_combo import (
    S3_FIRST_BATCH,
    S3_LAST_BATCH,
    generate_s3_messages,
)
from .validator import validate_pending_contacts


ProgressCallback = Callable[[dict[str, object]], None]
StageCallback = Callable[[str, str, dict[str, object] | None], None]
MessageGenerator = Callable[..., dict[str, object]]


@dataclass(frozen=True)
class StrategyRoute:
    name: str
    first_batch: int
    last_batch: int
    generator: MessageGenerator
    continuation_from: int | None = None

    def supports_batch(self, batch_no: int) -> bool:
        return self.first_batch <= batch_no <= self.last_batch or (
            self.continuation_from is not None and batch_no >= self.continuation_from
        )

    @property
    def batch_description(self) -> str:
        base = f"{self.first_batch}-{self.last_batch}"
        if self.continuation_from is None:
            return base
        return f"{base} or {self.continuation_from}+"


STRATEGY_ROUTES = {
    "s1": StrategyRoute(
        "s1",
        S1_FIRST_BATCH,
        S1_LAST_BATCH,
        generate_s1_messages,
        continuation_from=S1_CONTINUATION_FIRST_BATCH,
    ),
    "s2": StrategyRoute("s2", S2_FIRST_BATCH, S2_LAST_BATCH, generate_s2_messages),
    "s3": StrategyRoute("s3", S3_FIRST_BATCH, S3_LAST_BATCH, generate_s3_messages),
}


def get_strategy_route(strategy: str) -> StrategyRoute:
    key = strategy.strip().lower()
    try:
        return STRATEGY_ROUTES[key]
    except KeyError as exc:
        available = ", ".join(STRATEGY_ROUTES)
        raise ValueError(f"Unknown strategy {strategy!r}; choose one of: {available}") from exc


def generate_strategy_messages(
    strategy: str,
    **kwargs: object,
) -> dict[str, object]:
    return get_strategy_route(strategy).generator(**kwargs)


def prepare_campaign(
    *,
    batch_no: int,
    sender_email: str,
    strategy: str = "s2",
    workers: int = 8,
    check_mx: bool = True,
    on_stage: StageCallback | None = None,
    on_validation_progress: ProgressCallback | None = None,
    on_generation_progress: ProgressCallback | None = None,
) -> dict[str, object]:
    """Prepare one batch and finish with a non-sending SMTP dry-run."""
    route = get_strategy_route(strategy)
    if not route.supports_batch(batch_no):
        raise ValueError(
            f"{route.name.upper()} uses batch {route.batch_description}; "
            f"batch {batch_no} is outside that range."
        )

    country_holds_applied = apply_country_holds(batch_no=batch_no)

    _stage(on_stage, "validate", "start")
    validation = validate_pending_contacts(
        batch_no=batch_no,
        limit=None,
        check_mx=check_mx,
        progress=on_validation_progress,
    )
    _stage(on_stage, "validate", "done", validation)

    _stage(on_stage, "generate", "start")
    generation = route.generator(
        batch_no=batch_no,
        limit=600,
        workers=workers,
        apply=True,
        overwrite=False,
        progress=on_generation_progress,
    )
    _stage(on_stage, "generate", "done", generation)
    if generation["interrupted"]:
        raise RuntimeError(
            "AI generation was interrupted. Run the same campaign command again to resume."
        )

    _stage(on_stage, "assign", "start")
    assignment = assign_batch_to_sender(
        batch_no=batch_no,
        sender_email=sender_email,
        apply=True,
    )
    _stage(on_stage, "assign", "done", assignment)
    if int(assignment["conflicting_assignments"]) > 0:
        raise RuntimeError(
            f"Batch {batch_no} is already assigned to another sender. No email was sent."
        )
    if not assignment["ready"] and int(assignment["missing_content"]) == 0:
        raise RuntimeError(f"Batch {batch_no} currently has no sendable creators.")

    _stage(on_stage, "dry_run", "start")
    dry_run = send_batch(
        batch_no=batch_no,
        sender_email=sender_email,
        limit=None,
        execute=False,
    )
    _stage(on_stage, "dry_run", "done", dry_run)

    return {
        "strategy": route.name,
        "batch_no": batch_no,
        "sender_email": assignment["sender_email"],
        "validation": validation,
        "generation": generation,
        "assignment": assignment,
        "dry_run": dry_run,
        "country_holds_applied": country_holds_applied,
        "skipped_generation_failed": int(generation["failed"]),
        "skipped_missing_content": int(assignment["missing_content"]),
    }


def send_prepared_campaign(
    *,
    batch_no: int,
    sender_email: str,
) -> dict[str, int]:
    """Send only the requested batch through its assigned account."""
    return send_batch(
        batch_no=batch_no,
        sender_email=sender_email,
        limit=None,
        execute=True,
    )


def _stage(
    callback: StageCallback | None,
    name: str,
    event: str,
    result: dict[str, object] | None = None,
) -> None:
    if callback is not None:
        callback(name, event, result)
