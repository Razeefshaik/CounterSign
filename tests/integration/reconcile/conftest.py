"""Data schemas are isolated; the financial advisory lock is database-wide."""

import asyncio

import pytest


@pytest.fixture
def poll_until_ready():
    async def poll(service):
        # Another worktree may hold the database-wide lock while testing its own schema.
        async with asyncio.timeout(60):
            while True:
                result = await service.poll_once()
                if not result.busy:
                    return result
                await asyncio.sleep(0.02)

    return poll
