"""Run a specific SQL migration file against the database."""

import asyncio
import sys
import asyncpg
from src.config import get_settings


async def main(migration_file: str) -> None:
    settings = get_settings()
    conn = await asyncpg.connect(settings.database_url, ssl="require")
    try:
        with open(migration_file) as f:
            sql = f.read()
        await conn.execute(sql)
        print(f"Migration applied: {migration_file}")
    finally:
        await conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_migration.py <migration_file>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
