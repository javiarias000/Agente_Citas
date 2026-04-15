#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from core.config import get_settings

async def check_tables():
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    tables = [
        "tool_call_logs",
        "appointments",
        "messages",
        "langchain_memory",
        "conversations",
        "agent_toggles",
        "user_profiles",
        "agent_states"
    ]

    async with async_session() as session:
        for table in tables:
            result = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = result.scalar()
            print(f"{table}: {count} rows")

if __name__ == "__main__":
    asyncio.run(check_tables())
