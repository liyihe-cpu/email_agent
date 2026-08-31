from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    target_database_url: str
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_collection: str = "creator_profiles"

    # 所有 AI 场景统一使用同一个 OpenAI-compatible 服务入口。
    llm_api_base_url: str = "https://api.siliconflow.cn/v1"
    llm_api_key: SecretStr = Field(default=SecretStr(""))
    llm_model: str = ""
    followup_llm_model: str = "zai-org/GLM-5.2"
    followup_mailbox_email: str = "andy@coojoy.cn"
    llm_timeout_seconds: int = Field(default=120, ge=10, le=600)
    llm_max_retries: int = Field(default=3, ge=0, le=10)

    smtp_execution_enabled: bool = False
    smtp_accounts_file: Path = PROJECT_ROOT / "runtime" / "smtp_accounts.json"

    imap_poll_seconds: int = Field(default=30, ge=5)

    max_inbound_body_chars: int = Field(default=16_000, ge=1_000, le=100_000)

    # Exact public Creator Pass page base, including its path prefix.
    form_public_base_url: str = "http://127.0.0.1:8000/creator"
    form_token_secret: SecretStr = Field(default=SecretStr(""))
    form_token_ttl_days: int = Field(default=90, ge=1, le=365)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
