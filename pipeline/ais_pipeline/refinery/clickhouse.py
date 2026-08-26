"""ClickHouse adapter: batched inserts into the lake tables.

positions is append-only; vessels_static and vessel_latest are
ReplacingMergeTree, so an "upsert" is a plain INSERT — the newest ts wins at
merge time and FINAL/argMax covers reads before the merge.
"""

from typing import Any

import clickhouse_connect

from .models import (
    LATEST_COLUMNS,
    POSITION_COLUMNS,
    STATIC_COLUMNS,
    LatestRow,
    PositionRow,
    StaticRow,
)

POSITIONS_TABLE = "positions"
STATIC_TABLE = "vessels_static"
LATEST_TABLE = "vessel_latest"


class ClickHouseWriter:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database
        self._client: Any = None

    async def start(self) -> None:
        self._client = await clickhouse_connect.get_async_client(
            host=self._host,
            port=self._port,
            username=self._user,
            password=self._password,
            database=self._database,
        )

    async def insert_positions(self, rows: list[PositionRow]) -> None:
        await self._insert(POSITIONS_TABLE, [r.as_tuple() for r in rows], POSITION_COLUMNS)

    async def insert_static(self, rows: list[StaticRow]) -> None:
        await self._insert(STATIC_TABLE, [r.as_tuple() for r in rows], STATIC_COLUMNS)

    async def insert_latest(self, rows: list[LatestRow]) -> None:
        await self._insert(LATEST_TABLE, [r.as_tuple() for r in rows], LATEST_COLUMNS)

    async def _insert(
        self,
        table: str,
        data: list[tuple[object, ...]],
        columns: tuple[str, ...],
    ) -> None:
        if not data or self._client is None:
            return
        await self._client.insert(table, data, column_names=list(columns))

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
