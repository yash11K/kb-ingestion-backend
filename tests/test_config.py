"""Tests for the configuration module."""

import os

import pytest

from src.config import Settings


class TestSettings:
    """Tests for the Settings class."""

    def test_loads_all_required_fields(self, monkeypatch):
        """Settings loads all required fields from environment variables."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
        monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("ALLOWLIST", '["*/text","*/richtext"]')
        monkeypatch.setenv("DENYLIST", '["*/container","*/page"]')

        settings = Settings()

        assert settings.database_url == "postgresql://user:pass@host:5432/db"
        assert settings.s3_bucket_name == "test-bucket"
        assert settings.allowlist == ["*/text", "*/richtext"]
        assert settings.denylist == ["*/container", "*/page"]

    def test_default_values(self, monkeypatch):
        """Settings uses correct defaults for optional fields."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
        monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("ALLOWLIST", '["*/text"]')
        monkeypatch.setenv("DENYLIST", '["*/container"]')

        settings = Settings()

        assert settings.aws_region == "us-east-1"
        assert settings.bedrock_model_id == "us.anthropic.claude-sonnet-4-20250514-v1:0"
        assert settings.aem_request_timeout == 30
        assert settings.auto_approve_threshold == 0.7
        assert settings.auto_reject_threshold == 0.2

    def test_overrides_defaults_from_env(self, monkeypatch):
        """Settings overrides defaults when env vars are set."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
        monkeypatch.setenv("S3_BUCKET_NAME", "my-bucket")
        monkeypatch.setenv("ALLOWLIST", '["*/text"]')
        monkeypatch.setenv("DENYLIST", '["*/container"]')
        monkeypatch.setenv("AWS_REGION", "eu-west-1")
        monkeypatch.setenv("AEM_REQUEST_TIMEOUT", "60")
        monkeypatch.setenv("AUTO_APPROVE_THRESHOLD", "0.8")
        monkeypatch.setenv("AUTO_REJECT_THRESHOLD", "0.3")

        settings = Settings()

        assert settings.aws_region == "eu-west-1"
        assert settings.aem_request_timeout == 60
        assert settings.auto_approve_threshold == 0.8
        assert settings.auto_reject_threshold == 0.3

    def test_missing_database_url_raises(self, monkeypatch, tmp_path):
        """Settings raises ValidationError when database_url is missing."""
        monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("ALLOWLIST", '["*/text"]')
        monkeypatch.setenv("DENYLIST", '["*/container"]')
        monkeypatch.delenv("DATABASE_URL", raising=False)

        # Point to an empty .env so the real .env doesn't supply DATABASE_URL
        empty_env = tmp_path / ".env"
        empty_env.write_text("")

        with pytest.raises(Exception):
            Settings(_env_file=str(empty_env))

    def test_missing_s3_bucket_name_raises(self, monkeypatch, tmp_path):
        """Settings raises ValidationError when s3_bucket_name is missing."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
        monkeypatch.setenv("ALLOWLIST", '["*/text"]')
        monkeypatch.setenv("DENYLIST", '["*/container"]')
        monkeypatch.delenv("S3_BUCKET_NAME", raising=False)

        # Point to an empty .env so the real .env doesn't supply S3_BUCKET_NAME
        empty_env = tmp_path / ".env"
        empty_env.write_text("")

        with pytest.raises(Exception):
            Settings(_env_file=str(empty_env))

    def test_comma_separated_lists(self, monkeypatch):
        """Settings parses comma-separated list values from env."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
        monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
        monkeypatch.setenv("ALLOWLIST", "*/accordionitem,*/text,*/richtext")
        monkeypatch.setenv("DENYLIST", "*/responsivegrid,*/container")

        settings = Settings()

        assert settings.allowlist == ["*/accordionitem", "*/text", "*/richtext"]
        assert settings.denylist == ["*/responsivegrid", "*/container"]
