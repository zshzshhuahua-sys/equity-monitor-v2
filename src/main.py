"""
股权激励监控面板 - 主程序入口
"""
import asyncio
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from src.database import init_db, close_db
from src.config import settings
from src.api import import_router, monitor_router, stocks_router, crawl_router
from src.services.crawl_scheduler_service import crawl_scheduler_service
from src.notifiers.email import email_notifier


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化数据库
    await init_db()
    print(f"✅ 数据库初始化完成: {settings.database_url}")

    # 启动爬虫调度器（夜间增量任务）
    crawl_scheduler_service.start()

    email_status = email_notifier.get_status()
    print(
        "📧 邮件状态: "
        f"enabled={email_status['enabled']} "
        f"host={email_status['smtp_host']} "
        f"recipients={email_status['recipients_count']} "
        f"issues={','.join(email_status['issues']) or 'none'}"
    )

    print(f"🚀 服务启动: http://{settings.host}:{settings.port}")

    yield

    # 关闭时清理资源
    crawl_scheduler_service.stop()
    await close_db()
    print("👋 服务已关闭")


# 创建FastAPI应用
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan
)

# 挂载静态文件
static_dir = Path(__file__).parent.parent / "web" / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# 注册API路由
app.include_router(import_router)
app.include_router(monitor_router)
app.include_router(stocks_router)
app.include_router(crawl_router)


@app.get("/", response_class=HTMLResponse)
async def root():
    """主页"""
    index_path = Path(__file__).parent.parent / "web" / "index.html"
    if index_path.exists():
        return index_path.read_text(encoding='utf-8')
    return """
    <!DOCTYPE html>
    <html>
    <head><title>股权激励监控面板</title></head>
    <body><h1>服务运行中，但找不到前端文件</h1></body>
    </html>
    """


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "version": settings.app_version}


def signal_handler(sig, frame):
    """信号处理"""
    print("\n收到终止信号，正在关闭...")
    sys.exit(0)


if __name__ == "__main__":
    import uvicorn
    
    # 注册信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 启动服务器 - 使用8001端口避免冲突
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=8001,
        reload=settings.debug,
        log_level="info"
    )
