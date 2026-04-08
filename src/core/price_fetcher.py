"""
价格获取服务
基于AKShare获取A股实时价格
"""
import asyncio
import logging
from datetime import datetime, time
from typing import List, Dict, Optional
import pytz
from sqlalchemy import select

from ..database import AsyncSessionLocal
from ..database.models import StockWatch, PriceCache
from ..api.akshare_client import akshare_client, StockPrice
from ..config import settings

logger = logging.getLogger(__name__)


class PriceFetcher:
    """价格获取服务"""
    
    def __init__(self):
        self.client = akshare_client
        self._is_running = False
    
    @staticmethod
    def is_trading_time() -> bool:
        """
        检查当前是否在A股交易时间（北京时间）

        A股交易时间：
        - 工作日：周一至周五（节假日除外）
        - 上午：09:30 - 11:30
        - 下午：13:00 - 15:00
        """
        # 使用北京时间
        tz = pytz.timezone('Asia/Shanghai')
        now = datetime.now(tz)

        # 检查是否为工作日（0=周一, 6=周日）
        if now.weekday() >= 5:  # 周六或周日
            return False

        current_time = now.time()

        # 上午交易时间：09:30 - 11:30
        morning_start = time(9, 30)
        morning_end = time(11, 30)

        # 下午交易时间：13:00 - 15:00
        afternoon_start = time(13, 0)
        afternoon_end = time(15, 0)

        is_morning = morning_start <= current_time <= morning_end
        is_afternoon = afternoon_start <= current_time <= afternoon_end

        return is_morning or is_afternoon
    
    async def fetch_watchlist_prices(self) -> Dict[str, StockPrice]:
        """
        获取监控列表中所有股票的价格
        
        Returns:
            Dict[full_code, StockPrice]
        """
        # 如果设置了仅交易时间监控，且当前非交易时间，尝试使用缓存价格
        if settings.monitor.trading_hours_only and not self.is_trading_time():
            # 尝试从缓存获取价格
            return await self._get_cached_prices()
        
        async with AsyncSessionLocal() as session:
            # 获取所有启用的监控股票
            stmt = select(StockWatch).where(StockWatch.is_active == True)
            result = await session.execute(stmt)
            stocks = result.scalars().all()
            
            if not stocks:
                return {}
            
            # 提取股票代码
            symbols = [stock.full_code for stock in stocks]
            
            # 分批获取（AKShare限制）
            all_prices = {}
            batch_size = 100
            
            for i in range(0, len(symbols), batch_size):
                batch = symbols[i:i + batch_size]
                try:
                    prices = await self.client.get_prices_batch(batch)
                    all_prices.update(prices)
                    
                    # 请求间隔，避免过于频繁
                    if i + batch_size < len(symbols):
                        await asyncio.sleep(1)
                        
                except Exception as e:
                    logger.warning("获取价格失败 (batch %d): %s", i // batch_size + 1, e)
                    continue
            
            return all_prices
    
    async def update_price_cache(self, prices: Dict[str, StockPrice]):
        """
        更新价格缓存到数据库
        
        Args:
            prices: Dict[full_code, StockPrice]
        """
        if not prices:
            return
        
        async with AsyncSessionLocal() as session:
            for full_code, price_data in prices.items():
                try:
                    # 查询是否已有缓存记录
                    stmt = select(PriceCache).where(PriceCache.symbol == price_data.symbol)
                    result = await session.execute(stmt)
                    cache = result.scalar_one_or_none()
                    
                    if cache:
                        # 更新现有记录
                        cache.last_price = price_data.current_price
                        cache.change_percent = price_data.change_percent
                        cache.last_updated = datetime.utcnow()
                    else:
                        # 创建新记录
                        cache = PriceCache(
                            symbol=price_data.symbol,
                            exchange=price_data.exchange,
                            full_code=price_data.full_code,
                            last_price=price_data.current_price,
                            change_percent=price_data.change_percent,
                            last_updated=datetime.utcnow()
                        )
                        session.add(cache)
                
                except Exception as e:
                    logger.error("更新价格缓存失败 %s: %s", full_code, e)
                    continue
            
            await session.commit()
    
    async def get_cached_prices(self, full_codes: Optional[List[str]] = None) -> Dict[str, PriceCache]:
        """
        获取缓存的价格数据

        Args:
            full_codes: 完整股票代码列表（可选），如 ["600519.SH", "000001.SZ"]

        Returns:
            Dict[symbol, PriceCache] - key为6位股票代码
        """
        async with AsyncSessionLocal() as session:
            if full_codes:
                stmt = select(PriceCache).where(PriceCache.full_code.in_(full_codes))
            else:
                stmt = select(PriceCache)
            
            result = await session.execute(stmt)
            caches = result.scalars().all()
            
            return {cache.symbol: cache for cache in caches}
    
    async def _get_cached_prices(self) -> Dict[str, StockPrice]:
        """
        从缓存获取价格数据（用于非交易时间）
        
        Returns:
            Dict[full_code, StockPrice]
        """
        async with AsyncSessionLocal() as session:
            # 获取所有启用的监控股票
            stmt = select(StockWatch).where(StockWatch.is_active == True)
            result = await session.execute(stmt)
            stocks = result.scalars().all()
            
            if not stocks:
                return {}
            
            # 获取这些股票的缓存价格
            full_codes = [stock.full_code for stock in stocks]
            cache_stmt = select(PriceCache).where(PriceCache.full_code.in_(full_codes))
            cache_result = await session.execute(cache_stmt)
            caches = cache_result.scalars().all()
            
            # 转换为StockPrice格式
            prices = {}
            for cache in caches:
                if cache.last_price and cache.last_price > 0:
                    prices[cache.full_code] = StockPrice(
                        symbol=cache.symbol,
                        exchange=cache.exchange,
                        full_code=cache.full_code,
                        name="",  # 缓存中可能没有名称
                        current_price=cache.last_price,
                        change_percent=cache.change_percent or 0,
                        update_time=cache.last_updated.strftime('%H:%M:%S') if cache.last_updated else ''
                    )
            
            return prices


# 全局价格获取器实例
price_fetcher = PriceFetcher()
