"""Run database migrations."""

import asyncio
from pathlib import Path

import asyncpg

from app.config import settings

MIGRATIONS_DIR = Path("migrations")


async def run_migrations():
    conn = await asyncpg.connect(settings.database_url)
    try:
        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for migration in migration_files:
            print(f"Running {migration.name}...")
            sql = migration.read_text()
            await conn.execute(sql)
            print(f"  Done.")
    finally:
        await conn.close()

    print("All migrations complete.")


if __name__ == "__main__":
    asyncio.run(run_migrations())
