"""Application configuration loaded from environment variables."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, EnvSettingsSource, PydanticBaseSettingsSource


class _CommaSeparatedEnvSource(EnvSettingsSource):
    """Env source that falls back to comma-separated parsing for list fields."""

    def decode_complex_value(self, field_name: str, field: FieldInfo, value: Any) -> Any:
        try:
            return super().decode_complex_value(field_name, field, value)
        except json.JSONDecodeError:
            if isinstance(value, str):
                return [item.strip() for item in value.split(",") if item.strip()]
            raise


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    # Database
    database_url: str  # SQLAlchemy async connection string (postgresql+asyncpg://...)

    # AWS
    aws_region: str = "us-east-1"
    s3_bucket_name: str
    bedrock_model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0"
    bedrock_kb_id: str = ""  # AWS Bedrock Knowledge Base ID

    # AEM
    aem_request_timeout: int = 30  # seconds

    # Validation thresholds
    auto_approve_threshold: float = 0.7
    auto_reject_threshold: float = 0.2

    # Component filtering (legacy — kept for backward compat with tests)
    allowlist: list[str] = []
    denylist: list[str] = []

    # Payload size threshold (bytes) for pre-filtering large AEM JSON
    max_payload_bytes: int = 500_000

    # Bedrock max output tokens per agent invocation
    bedrock_max_tokens: int = 16_000

    # Batch threshold for splitting large node sets into sequential agent calls
    batch_threshold: int = 8

    # Concurrency
    max_concurrent_jobs: int = 3  # MAX_CONCURRENT_JOBS env var

    # Haiku discovery agent
    haiku_model_id: str = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
    enable_haiku_discovery: bool = True
    haiku_max_input_tokens: int = 150_000  # max tokens to send to Haiku in one call

    # URL denylist patterns for deep link filtering
    url_denylist_patterns: list[str] = [
        "/reservation",
        "/login",
        "/account",
        "/search",
        "/booking",
        "/checkout",
        "/payment",
        "/registration",
        "/reset-password",
        "/demo",
    ]

    namespace_list: list[str] = [  # NAMESPACE_LIST env var
        "locations",
        "products-and-services",
        "protections-and-coverages",
        "rental-addons",
        "long-term-car-rental",
        "one-way-car-rentals",
        "miles-points-and-partners",
        "meetings-and-groups",
        "car-sales",
        "faq",
        "customer-service",
        "travel-guides",
    ]
    locale_region_map: dict[str, str] = {  # LOCALE_REGION_MAP env var (JSON)
        "en": "nam",
        "en-us": "nam",
        "en-ca": "nam",
        "en-gb": "emea",
        "en-ie": "emea",
        "de": "emea",
        "fr": "emea",
        "en-au": "apac",
        "en-nz": "apac",
    }
    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError(
                f"DATABASE_URL must use the SQLAlchemy asyncpg dialect "
                f"(expected 'postgresql+asyncpg://...', got '{v[:30]}...')"
            )
        return v

    @field_validator("batch_threshold")
    @classmethod
    def batch_threshold_min(cls, v: int) -> int:
        return max(1, v)

    model_config = {"env_file": ".env"}

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _CommaSeparatedEnvSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
