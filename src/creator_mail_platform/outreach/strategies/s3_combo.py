from __future__ import annotations

from .s1_direct_pitch import generate_direct_pitch_messages


S3_FIRST_BATCH = 51
S3_LAST_BATCH = 75


def generate_s3_messages(**kwargs: object) -> dict[str, object]:
    """Generate S1 direct pitch plus a personalized Creator Pass link."""
    return generate_direct_pitch_messages(
        strategy_no=3,
        first_batch=S3_FIRST_BATCH,
        last_batch=S3_LAST_BATCH,
        include_creator_pass=True,
        **kwargs,
    )
