from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


SCHEMA = "creator_mail"


class Base(DeclarativeBase):
    pass


class ActivityContact(Base):
    __tablename__ = "activity_contacts"
    __table_args__ = (
        Index("ix_activity_contacts_batch_send", "batch_no", "send_status"),
        Index("ix_activity_contacts_sender_sent", "sender_email", "sent_at"),
        {"schema": SCHEMA},
    )

    # 每个达人独立保留一行；不同达人可以使用同一个邮箱。
    creator_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    email: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    batch_no: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    platform: Mapped[str | None] = mapped_column(String(50))
    handle: Mapped[str | None] = mapped_column(String(255))
    follower_count: Mapped[int | None] = mapped_column(BigInteger)
    country: Mapped[str | None] = mapped_column(String(128))
    language: Mapped[str | None] = mapped_column(String(128))
    primary_category: Mapped[str | None] = mapped_column(Text)
    profile_bio: Mapped[str | None] = mapped_column(Text)
    analysis_note: Mapped[str | None] = mapped_column(Text)
    source_data_version: Mapped[str] = mapped_column(String(100), nullable=False)

    sender_email: Mapped[str | None] = mapped_column(String(320), index=True)
    send_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending", index=True
    )
    email_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown", server_default="unknown", index=True
    )
    status_reason: Mapped[str | None] = mapped_column(String(64))
    smtp_code: Mapped[int | None] = mapped_column(Integer)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    brief_code: Mapped[str | None] = mapped_column(String(128), index=True)
    offered_brief_codes: Mapped[list[str] | None] = mapped_column(JSONB)
    sent_subject: Mapped[str | None] = mapped_column(Text)
    sent_body_text: Mapped[str | None] = mapped_column(Text)
    outbound_message_id: Mapped[str | None] = mapped_column(String(512), index=True)

    form_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    form_response_json: Mapped[dict | None] = mapped_column(JSONB)
    reward_status: Mapped[str | None] = mapped_column(String(32), index=True)

    # Only the latest valid reply is retained; reply_count preserves the total.
    reply_type: Mapped[str | None] = mapped_column(String(32), index=True)
    reply_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    reply_subject: Mapped[str | None] = mapped_column(Text)
    reply_body_text: Mapped[str | None] = mapped_column(Text)


class MailboxCheckpoint(Base):
    __tablename__ = "mailbox_checkpoints"
    __table_args__ = ({"schema": SCHEMA},)

    mailbox_email: Mapped[str] = mapped_column(String(320), primary_key=True)
    uidvalidity: Mapped[str] = mapped_column(String(64), nullable=False)
    last_uid: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")


class CampaignResponseProfile(Base):
    """One compact row containing a creator's complete response conversation."""

    __tablename__ = "campaign_response_profiles"
    __table_args__ = (
        Index("ix_response_profiles_status", "status", "updated_at"),
        Index("ix_response_profiles_mailbox_recipient", "mailbox_email", "recipient_email"),
        Index(
            "ix_response_profiles_messages_gin",
            "messages_json",
            postgresql_using="gin",
        ),
        {"schema": SCHEMA},
    )

    creator_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    batch_no: Mapped[int | None] = mapped_column(Integer, index=True)
    mailbox_email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    recipient_email: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    messages_json: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    analysis_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="draft", server_default="draft", index=True
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
