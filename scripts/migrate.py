"""Run database migrations."""

import asyncio
import ssl
from pathlib import Path

import asyncpg

from app.config import settings

MIGRATIONS_DIR = Path("migrations")


async def run_migrations():
    url = settings.database_url
    kwargs = {}
    if "localhost" not in url and "127.0.0.1" not in url and ".railway.internal" not in url and "sslmode=disable" not in url:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl"] = ctx
    conn = await asyncio.wait_for(asyncpg.connect(url, **kwargs), timeout=15)
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
    try:
        asyncio.run(run_migrations())
    except Exception as e:
        print(f"Migration failed: {e}")
        print("Server will start anyway — migrations may need to be retried.")
