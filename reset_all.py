"""Reset script: truncate all DB tables and empty the S3 bucket."""

import asyncio
import boto3
import asyncpg
from src.config import get_settings


async def reset_db(database_url: str) -> None:
    conn = await asyncpg.connect(database_url, ssl="require")
    try:
        for table in ("kb_files", "ingestion_jobs", "revalidation_jobs"):
            exists = await conn.fetchval(
                "SELECT to_regclass($1) IS NOT NULL", f"public.{table}"
            )
            if exists:
                await conn.execute(f"TRUNCATE {table} CASCADE")
                print(f"DB: truncated {table}")
            else:
                print(f"DB: {table} does not exist, skipping")
    finally:
        await conn.close()


def reset_s3(bucket_name: str, region: str) -> None:
    s3 = boto3.client("s3", region_name=region)
    paginator = s3.get_paginator("list_objects_v2")
    deleted = 0

    for page in paginator.paginate(Bucket=bucket_name):
        objects = page.get("Contents", [])
        if not objects:
            continue
        keys = [{"Key": obj["Key"]} for obj in objects]
        s3.delete_objects(Bucket=bucket_name, Delete={"Objects": keys})
        deleted += len(keys)

    print(f"S3: deleted {deleted} objects from {bucket_name}.")


async def main() -> None:
    settings = get_settings()
    await reset_db(settings.database_url)
    reset_s3(settings.s3_bucket_name, settings.aws_region)
    print("Done — clean slate.")


if __name__ == "__main__":
    asyncio.run(main())
