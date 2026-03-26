"""Reset script: truncate all DB tables and empty the S3 bucket."""

import asyncio
import boto3
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from src.config import get_settings


async def reset_db(database_url: str) -> None:
    engine = create_async_engine(
        database_url,
        connect_args={"ssl": "require", "statement_cache_size": 0},
        echo=False,
    )
    try:
        async with engine.begin() as conn:
            for table in (
                "kb_files",
                "ingestion_jobs",
                "revalidation_jobs",
                "sources",
                "nav_tree_cache",
                "deep_links",
            ):
                result = await conn.execute(
                    text("SELECT to_regclass(:tbl) IS NOT NULL"),
                    {"tbl": f"public.{table}"},
                )
                exists = result.scalar()
                if exists:
                    await conn.execute(text(f"TRUNCATE {table} CASCADE"))
                    print(f"DB: truncated {table}")
                else:
                    print(f"DB: {table} does not exist, skipping")
    finally:
        await engine.dispose()


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
