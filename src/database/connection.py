"""
数据库连接管理
支持异步操作
"""
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from .models import Base

# 数据库文件路径
DB_DIR = Path(__file__).parent.parent.parent / "data"
DB_PATH = DB_DIR / "equity_monitor.db"

# 确保数据目录存在
DB_DIR.mkdir(parents=True, exist_ok=True)

# 创建异步引擎
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True
)

# 创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False
)


async def init_db():
    """初始化数据库，创建所有表（触发器已废除，由 Python 层全权控制 latest）"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """获取数据库会话的依赖函数"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def close_db():
    """关闭数据库连接"""
    await engine.dispose()
