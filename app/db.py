"""Database connection pool management for asyncpg + pgvector."""

import ssl

import asyncpg
from pgvector.asyncpg import register_vector

from app.config import settings

_pool: asyncpg.Pool | None = None


def _ssl_context():
    """Return SSL context if DATABASE_URL suggests a remote host."""
    url = settings.database_url
    if "localhost" in url or "127.0.0.1" in url or ".railway.internal" in url or "sslmode=disable" in url:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        ssl_ctx = _ssl_context()
        kwargs = {}
        if ssl_ctx:
            kwargs["ssl"] = ssl_ctx
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=2,
            max_size=10,
            init=_init_connection,
            **kwargs,
        )
    return _pool


async def _init_connection(conn: asyncpg.Connection):
    await register_vector(conn)
    await conn.execute("SET ivfflat.probes = 10")


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
