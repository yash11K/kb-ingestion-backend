# Configuration

All configuration is loaded from environment variables (with `.env` file support) via `pydantic-settings`.

## Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DATABASE_URL` | string | (required) | NeonDB PostgreSQL connection string |
| `AWS_REGION` | string | `us-east-1` | AWS region for Bedrock and S3 |
| `S3_BUCKET_NAME` | string | (required) | S3 bucket for approved Markdown files |
| `BEDROCK_MODEL_ID` | string | `us.anthropic.claude-sonnet-4-20250514-v1:0` | Bedrock model for both agents |
| `AEM_REQUEST_TIMEOUT` | int | `30` | HTTP timeout (seconds) for AEM endpoint fetches |
| `AUTO_APPROVE_THRESHOLD` | float | `0.7` | Validation score at or above which files are auto-approved |
| `AUTO_REJECT_THRESHOLD` | float | `0.2` | Validation score below which files are auto-rejected |
| `MAX_PAYLOAD_BYTES` | int | `500000` | Payload size threshold for logging warnings on large AEM JSON |
| `ALLOWLIST` | JSON array | (required) | AEM component types to extract |
| `DENYLIST` | JSON array | (required) | AEM component types to skip |

## Component Filtering

The allowlist and denylist use glob-style suffix matching. The `*/` prefix is stripped and the remainder is matched via `endswith()`.

**Default Allowlist:**
```json
[
  "*/accordionitem", "*/text", "*/richtext", "*/tabitem",
  "*/termsandconditions", "*/policytext", "*/contentfragment",
  "*/teaser", "*/hero", "*/accordion", "*/tabs"
]
```

**Default Denylist:**
```json
[
  "*/responsivegrid", "*/container", "*/page", "*/header",
  "*/footer", "*/navigation", "*/breadcrumb", "*/image",
  "*/button", "*/separator", "*/spacer", "*/experiencefragment",
  "*/languagenavigation", "*/search"
]
```

Denylist takes precedence over allowlist. If a component type matches both, it is excluded.

## Validation Thresholds

The score-based routing thresholds are configurable:

```
Score ≥ AUTO_APPROVE_THRESHOLD (0.7)  →  auto-approved → S3 upload
Score ≥ AUTO_REJECT_THRESHOLD  (0.2)  →  pending_review (human queue)
Score <  AUTO_REJECT_THRESHOLD (0.2)  →  auto-rejected
```

Adjusting these thresholds changes the balance between automation and human review. Lower `AUTO_APPROVE_THRESHOLD` means more files are auto-approved; higher `AUTO_REJECT_THRESHOLD` means fewer files reach the review queue.

## Example .env

```env
DATABASE_URL=postgresql://user:pass@host/dbname?sslmode=require
AWS_REGION=us-east-1
S3_BUCKET_NAME=my-kb-bucket
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-20250514-v1:0
AEM_REQUEST_TIMEOUT=30
AUTO_APPROVE_THRESHOLD=0.7
AUTO_REJECT_THRESHOLD=0.2
MAX_PAYLOAD_BYTES=500000
ALLOWLIST=["*/accordionitem","*/text","*/richtext","*/tabitem"]
DENYLIST=["*/responsivegrid","*/container","*/page"]
```

Note: The `ALLOWLIST` and `DENYLIST` values can be either JSON arrays or comma-separated strings. The custom `_CommaSeparatedEnvSource` in `config.py` handles both formats.

## Settings Class

```python
class Settings(BaseSettings):
    database_url: str
    aws_region: str = "us-east-1"
    s3_bucket_name: str
    bedrock_model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0"
    aem_request_timeout: int = 30
    auto_approve_threshold: float = 0.7
    auto_reject_threshold: float = 0.2
    allowlist: list[str]
    denylist: list[str]
    max_payload_bytes: int = 500_000

    model_config = {"env_file": ".env"}
```

Settings are cached via `@lru_cache` on `get_settings()` so they're loaded once at startup.
