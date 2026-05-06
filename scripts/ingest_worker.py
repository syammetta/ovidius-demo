"""Run the durable ingestion worker loop."""

import asyncio
import logging

from app.db import get_pool, close_pool
from app.ingestion.worker import run_worker_loop


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await get_pool()
    try:
        await run_worker_loop()
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
