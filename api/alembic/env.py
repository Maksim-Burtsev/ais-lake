"""Async alembic env. No metadata: the schema is raw SQL (PostGIS types that
SQLAlchemy would only get half right), so autogenerate is deliberately off."""

import asyncio
import os

from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from alembic import context

URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://ais:ais-dev@localhost:5432/ais")


def run_offline() -> None:
    context.configure(url=URL, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = async_engine_from_config({"sqlalchemy.url": URL}, poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.run_sync(lambda c: context.configure(connection=c))
        await conn.run_sync(lambda _: context.run_migrations())
        await conn.commit()
    await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
