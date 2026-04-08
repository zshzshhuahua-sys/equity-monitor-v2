"""
监控服务
整合价格获取、价差计算、预警判断

修复内容：
1. 增加 asyncio.Lock 防重入
2. 启动时不再手动 await _monitor_job()，由 scheduler 触发首次执行
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict, Callable
from dataclasses import dataclass
from sqlalchemy import select

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..database import AsyncSessionLocal
from ..database.models import StockWatch, PriceCache, AlertLog
from ..config import settings
from .price_fetcher import price_fetcher
from .diff_calculator import diff_calculator, PriceDiff, AlertLevel
from .alert_rules import alert_engine, AlertType

logger = logging.getLogger(__name__)


@dataclass
class MonitorResult:
    """监控结果"""
    symbol: str
    full_code: str
    current_price: float
    strike_price: float
    diff_percent: float
    alert_triggered: bool
    alert_level: Optional[AlertLevel]
    alert_id: Optional[int]


class MonitorService:
    """监控服务"""

    def __init__(self):
        self.scheduler: Optional[AsyncIOScheduler] = None
        self._is_running = False
        self._job_id = "price_monitor"
        self._callbacks: List[Callable] = []
        self._job_lock = asyncio.Lock()  # 防重入锁

    def is_running(self) -> bool:
        """检查监控是否正在运行"""
        return self._is_running and self.scheduler is not None and self.scheduler.running

    async def start(self):
        """启动监控服务"""
        if self.is_running():
            logger.info("监控服务已在运行")
            return

        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_job(
            self._monitor_job,
            trigger=IntervalTrigger(seconds=settings.monitor.interval_seconds),
            id=self._job_id,
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.start()
        self._is_running = True
        logger.info("监控服务已启动，轮询间隔: %d秒", settings.monitor.interval_seconds)

    async def stop(self):
        """停止监控服务"""
        if not self.is_running():
            return

        if self.scheduler:
            self.scheduler.remove_job(self._job_id)
            self.scheduler.shutdown(wait=False)
            self.scheduler = None

        self._is_running = False
        logger.info("监控服务已停止")
    
    async def _monitor_job(self):
        """监控任务（带防重入锁）"""
        async with self._job_lock:
            try:
                # 检查是否在交易时间
                if settings.monitor.trading_hours_only and not price_fetcher.is_trading_time():
                    return

                logger.info("[%s] 开始价格监控...", datetime.now().strftime('%H:%M:%S'))

                # 1. 获取最新价格
                prices = await price_fetcher.fetch_watchlist_prices()

                if not prices:
                    logger.info("没有需要监控的股票")
                    return

                logger.info("获取到 %d 只股票的价格", len(prices))

                # 2. 更新价格缓存
                await price_fetcher.update_price_cache(prices)

                # 3. 获取监控列表（带执行价格）
                async with AsyncSessionLocal() as session:
                    stmt = select(StockWatch).where(StockWatch.is_active == True)
                    result = await session.execute(stmt)
                    stocks = result.scalars().all()

                    monitor_results = []

                    for stock in stocks:
                        if stock.full_code not in prices:
                            continue

                        price_data = prices[stock.full_code]

                        # 计算价差
                        diff_result = diff_calculator.calculate(
                            current_price=price_data.current_price,
                            strike_price=stock.strike_price,
                            symbol=stock.symbol,
                            full_code=stock.full_code,
                            name=stock.name
                        )

                        # 判断是否需要预警
                        should_alert, alert_level, threshold = alert_engine.should_alert(
                            symbol=stock.symbol,
                            diff_percent=diff_result.diff_percent / 100,  # 转换为小数
                            custom_threshold=stock.custom_threshold
                        )

                        alert_id = None

                        # 如果需要预警，记录到数据库
                        if should_alert:
                            alert_log = AlertLog(
                                stock_id=stock.id,
                                alert_type=AlertType.THRESHOLD_BREACH.value,
                                threshold_value=threshold,
                                trigger_price=price_data.current_price,
                                price_diff_percent=diff_result.diff_percent,
                                is_acknowledged=False,
                                created_at=datetime.utcnow()
                            )
                            session.add(alert_log)
                            await session.flush()
                            alert_id = alert_log.id

                            logger.warning(
                                "预警触发: %s (%s) - 价差: %+.2f%% - %s",
                                stock.full_code, stock.name,
                                diff_result.diff_percent, alert_level.value
                            )

                        monitor_results.append(MonitorResult(
                            symbol=stock.symbol,
                            full_code=stock.full_code,
                            current_price=price_data.current_price,
                            strike_price=stock.strike_price,
                            diff_percent=diff_result.diff_percent,
                            alert_triggered=should_alert,
                            alert_level=alert_level if should_alert else None,
                            alert_id=alert_id
                        ))

                    await session.commit()

                    # 触发回调
                    for callback in self._callbacks:
                        try:
                            if asyncio.iscoroutinefunction(callback):
                                await callback(monitor_results)
                            else:
                                callback(monitor_results)
                        except Exception as e:
                            logger.error("回调执行失败: %s", e)

                    logger.info("监控完成: %d 只股票已处理", len(monitor_results))

            except Exception as e:
                logger.exception("监控任务失败: %s", e)
    
    def add_callback(self, callback: Callable):
        """
        添加监控结果回调函数
        
        Args:
            callback: 回调函数，接收 List[MonitorResult] 参数
        """
        self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable):
        """移除回调函数"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)


# 全局监控服务实例
monitor_service = MonitorService()
