"""Async SQLAlchemy engine/session setup.

DATA_MODEL.md §1 is the source of truth for the schema itself.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


# 【卷 四.二.一】记住这两行：_engine 和 _session_factory 是模块加载时创建的全局单例，
# 【卷】整个进程生命周期只有这一份连接池。这对 app/（FastAPI/Uvicorn，一个进程从启动到关闭
# 【卷】只有一个 asyncio 事件循环）是完全正确的设计——asyncpg 的连接是"绑定在创建它的那个
# 【卷】事件循环上"的，只要事件循环不变，单例连接池就能安全地反复复用。
# 【卷】——先记住这个前提，六.三.三（worker/tasks.py 的 _persist_assistant_message）会讲到
# 【卷】worker 进程的执行模型跟这个前提不一样，同样的单例写法在那边会导致一个真实的运行时报错。
_engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with _session_factory() as session:
        yield session


def get_engine():
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return _session_factory
