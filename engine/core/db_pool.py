"""
engine/core/db_pool.py
========================
Async SQLite connection pool for the UC engine.

Uses a ``asyncio.Queue`` of ``aiosqlite.Connection`` objects to provide
bounded concurrent database access without hitting SQLite's write-lock
limitation.  All operations are coroutine-friendly.

Usage
-----
    pool = DatabasePool("path/to/db.sqlite", pool_size=4)
    await pool.initialize()

    async with pool.acquire() as conn:
        cursor = await conn.execute("SELECT 1")
        row = await cursor.fetchone()

    await pool.close()
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)


class DatabasePool:
    """
    Bounded async SQLite connection pool.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.  Created if it doesn't exist.
    pool_size : int
        Maximum number of concurrent connections in the pool.
    pragmas : dict[str, str], optional
        SQLite PRAGMA statements applied to every new connection.
        Defaults to WAL journal mode + foreign-key enforcement.
    """

    _DEFAULT_PRAGMAS = {
        "journal_mode": "WAL",
        "foreign_keys": "ON",
        "synchronous": "NORMAL",
        "busy_timeout": "5000",
    }

    def __init__(
        self,
        db_path: str,
        pool_size: int = 4,
        pragmas: Optional[dict] = None,
    ):
        self.db_path = db_path
        self.pool_size = pool_size
        self.pragmas = {**self._DEFAULT_PRAGMAS, **(pragmas or {})}
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=pool_size)
        self._initialized = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create all connections and fill the pool queue."""
        if self._initialized:
            return

        try:
            import aiosqlite
        except ImportError as exc:
            raise ImportError(
                "aiosqlite is required for DatabasePool. "
                "Install it with: pip install aiosqlite"
            ) from exc

        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

        for _ in range(self.pool_size):
            conn = await aiosqlite.connect(self.db_path)
            conn.row_factory = aiosqlite.Row
            for pragma, value in self.pragmas.items():
                await conn.execute(f"PRAGMA {pragma} = {value}")
            await conn.commit()
            await self._queue.put(conn)

        self._initialized = True
        logger.info(
            "[DBPool] Initialised %d connection(s) to %s", self.pool_size, self.db_path
        )

    async def close(self) -> None:
        """Close all connections in the pool."""
        while not self._queue.empty():
            conn = self._queue.get_nowait()
            await conn.close()
        self._initialized = False
        logger.info("[DBPool] All connections closed.")

    # ── Context manager ───────────────────────────────────────────────────────

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator:
        """
        Async context manager that checks out a connection from the pool and
        returns it when the block exits.

        Example
        -------
        ::

            async with pool.acquire() as conn:
                await conn.execute("INSERT INTO ...")
                await conn.commit()
        """
        if not self._initialized:
            await self.initialize()

        conn = await asyncio.wait_for(self._queue.get(), timeout=30)
        try:
            yield conn
        except Exception:
            # On error, roll back any pending transaction to leave the
            # connection in a clean state before returning it to the pool.
            try:
                await conn.rollback()
            except Exception:
                pass
            raise
        finally:
            await self._queue.put(conn)

    # ── Convenience helpers ───────────────────────────────────────────────────

    async def execute(self, sql: str, parameters: tuple = ()) -> list:
        """
        Execute a single SQL statement and return all rows as a list of dicts.

        Suitable for simple queries that don't need explicit transaction control.
        """
        async with self.acquire() as conn:
            cursor = await conn.execute(sql, parameters)
            rows = await cursor.fetchall()
            await conn.commit()
            return [dict(row) for row in rows]

    async def executemany(self, sql: str, parameters_seq: list) -> None:
        """
        Execute a SQL statement for each parameter tuple in ``parameters_seq``.
        Wraps the entire batch in a single transaction.
        """
        async with self.acquire() as conn:
            await conn.executemany(sql, parameters_seq)
            await conn.commit()
