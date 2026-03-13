"""Infrastructure diagnostic script.

Checks database connectivity and S3 write access to help troubleshoot
pipeline errors. Run with:

    python check_infra.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Colours for terminal output
# ---------------------------------------------------------------------------
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✔ {msg}{RESET}")


def fail(msg: str) -> None:
    print(f"  {RED}✘ {msg}{RESET}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠ {msg}{RESET}")


# ---------------------------------------------------------------------------
# 1. Database checks
# ---------------------------------------------------------------------------
async def check_database() -> bool:
    print("\n── Database (asyncpg → NeonDB) ──")

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        fail("DATABASE_URL is not set in .env")
        return False

    # Mask password for display
    masked = db_url
    try:
        from urllib.parse import urlparse
        parsed = urlparse(db_url)
        masked = db_url.replace(parsed.password or "", "***")
    except Exception:
        pass
    print(f"  URL: {masked}")

    try:
        import asyncpg
    except ImportError:
        fail("asyncpg is not installed — run: pip install asyncpg")
        return False

    # --- Single connection test ---
    print("\n  [1/3] Single connection...")
    try:
        t0 = time.perf_counter()
        conn = await asyncpg.connect(db_url, ssl="require", timeout=10)
        elapsed = time.perf_counter() - t0
        version = await conn.fetchval("SELECT version()")
        await conn.close()
        ok(f"Connected in {elapsed:.2f}s — {version[:60]}...")
    except Exception as exc:
        fail(f"Connection failed: {exc}")
        return False

    # --- Pool test (mimics app behaviour) ---
    print("  [2/3] Connection pool (min=2, max=5)...")
    pool = None
    try:
        pool = await asyncpg.create_pool(
            db_url, ssl="require", min_size=2, max_size=5, timeout=10,
        )
        async with pool.acquire() as conn:
            row = await conn.fetchval("SELECT 1")
            assert row == 1
        ok("Pool acquired and query succeeded")
    except Exception as exc:
        fail(f"Pool test failed: {exc}")
        return False
    finally:
        if pool:
            await pool.close()

    # --- Sustained connection test (catches idle-timeout drops) ---
    print("  [3/3] Sustained connection (hold 5s then query)...")
    try:
        conn = await asyncpg.connect(db_url, ssl="require", timeout=10)
        await asyncio.sleep(5)
        row = await conn.fetchval("SELECT 1")
        assert row == 1
        await conn.close()
        ok("Connection survived 5s idle + query")
    except Exception as exc:
        fail(f"Connection dropped after idle: {exc}")
        warn("NeonDB serverless may be suspending idle connections.")
        warn("Consider setting a shorter pool idle timeout or using keepalives.")
        return False

    return True


# ---------------------------------------------------------------------------
# 2. S3 checks
# ---------------------------------------------------------------------------
def check_s3() -> bool:
    print("\n── S3 (boto3) ──")

    bucket = os.getenv("S3_BUCKET_NAME")
    region = os.getenv("AWS_REGION", "us-east-1")

    if not bucket:
        fail("S3_BUCKET_NAME is not set in .env")
        return False
    print(f"  Bucket: {bucket}  Region: {region}")

    try:
        import boto3
        from botocore.exceptions import (
            ClientError,
            NoCredentialsError,
            PartialCredentialsError,
        )
    except ImportError:
        fail("boto3 is not installed — run: pip install boto3")
        return False

    # --- Credential check ---
    print("\n  [1/3] AWS credentials...")
    try:
        sts = boto3.client("sts", region_name=region)
        identity = sts.get_caller_identity()
        ok(f"Authenticated as {identity['Arn']}")
    except NoCredentialsError:
        fail("No AWS credentials found. Configure via env vars, ~/.aws/credentials, or IAM role.")
        return False
    except PartialCredentialsError as exc:
        fail(f"Incomplete credentials: {exc}")
        return False
    except ClientError as exc:
        fail(f"STS call failed: {exc}")
        return False

    s3 = boto3.client("s3", region_name=region)

    # --- Bucket exists / accessible ---
    print("  [2/3] Bucket access (HeadBucket)...")
    try:
        s3.head_bucket(Bucket=bucket)
        ok(f"Bucket '{bucket}' exists and is accessible")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "404":
            fail(f"Bucket '{bucket}' does not exist")
        elif code == "403":
            fail(f"Access denied to bucket '{bucket}' — check bucket policy / IAM permissions")
        else:
            fail(f"HeadBucket error: {exc}")
        return False

    # --- PutObject test ---
    print("  [3/3] Write test (PutObject)...")
    test_key = "_infra_check/test-write.txt"
    try:
        s3.put_object(
            Bucket=bucket,
            Key=test_key,
            Body=b"infrastructure check",
            ContentType="text/plain",
        )
        ok(f"PutObject succeeded — key: {test_key}")

        # Clean up
        s3.delete_object(Bucket=bucket, Key=test_key)
        ok("Cleanup: test object deleted")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "AccessDenied":
            fail(f"PutObject AccessDenied — your IAM identity lacks s3:PutObject on {bucket}")
            warn("Required permissions: s3:PutObject, s3:DeleteObject on the bucket")
        else:
            fail(f"PutObject failed: {exc}")
        return False

    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> None:
    print("=" * 50)
    print("  Infrastructure Diagnostic Check")
    print("=" * 50)

    db_ok = await check_database()
    s3_ok = check_s3()

    print("\n" + "=" * 50)
    print("  Summary")
    print("=" * 50)
    print(f"  Database: {GREEN + 'OK' + RESET if db_ok else RED + 'FAIL' + RESET}")
    print(f"  S3:       {GREEN + 'OK' + RESET if s3_ok else RED + 'FAIL' + RESET}")

    if not db_ok or not s3_ok:
        print(f"\n  {YELLOW}Fix the failing checks above, then re-run the pipeline.{RESET}")
        sys.exit(1)
    else:
        print(f"\n  {GREEN}All checks passed — infrastructure looks good.{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
