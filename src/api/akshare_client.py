"""
A股数据客户端封装 - 腾讯财经接口
支持沪深京所有A股实时行情获取

修复内容：
1. requests -> httpx.AsyncClient (真正异步)
2. 单股单请求 -> 批量请求 (qt.gtimg.cn 支持 comma 分隔)
3. 增加指数退避重试
4. 用 asyncio.Lock 替代竞态限频
5. per-symbol TTL 缓存
6. print -> logging
"""
import asyncio
import logging
import random
import time
import re
from typing import List, Dict, Optional, Tuple, Any, Set
from dataclasses import dataclass, field
import httpx

from ..utils.validators import detect_exchange

logger = logging.getLogger(__name__)


class PriceFetchError(Exception):
    """价格获取异常基类"""
    pass


class UpstreamRateLimited(PriceFetchError):
    """上游限流"""
    pass


class UpstreamTimeout(PriceFetchError):
    """上游超时"""
    pass


class UpstreamInvalidResponse(PriceFetchError):
    """上游响应无效"""
    pass


class UpstreamConnectionError(PriceFetchError):
    """上游连接失败"""
    pass


@dataclass
class StockPrice:
    """股票价格数据"""
    symbol: str
    exchange: str
    full_code: str
    name: str
    current_price: float
    change_percent: float
    update_time: str


@dataclass
class _CacheEntry:
    """带时间戳的缓存条目"""
    data: StockPrice
    timestamp: float


class AKShareClient:
    """
    AKShare A股数据客户端 - 使用腾讯财经接口

    修复点：
    - httpx.AsyncClient 替代 requests (真正异步)
    - 批量接口替代单股请求
    - 全局 RateLimiter 替代竞态判断
    - per-symbol TTL 缓存
    - 指数退避重试
    """

    # 腾讯批量接口一次最多代码数
    BATCH_MAX_CODES = 80
    # 全局请求间隔（秒）
    GLOBAL_RATE_LIMIT = 0.2
    # 单符号缓存 TTL（秒）
    SYMBOL_CACHE_TTL = 30

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        # per-symbol TTL 缓存: full_code -> _CacheEntry
        self._cache: Dict[str, _CacheEntry] = {}
        # 全局限频锁
        self._rate_limit_lock = asyncio.Lock()
        self._last_request_time: float = 0
        # 重试配置
        self._max_retries = 3
        self._retry_backoff_base = 0.5

    # ---- 生命周期 ----

    async def _get_client(self) -> httpx.AsyncClient:
        """懒加载 HTTP 客户端（连接池复用）"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=3.0,
                    read=5.0,
                    write=3.0,
                    pool=10.0,
                ),
                limits=httpx.Limits(
                    max_keepalive_connections=20,
                    max_connections=100,
                ),
                follow_redirects=True,
            )
        return self._client

    async def close(self):
        """关闭客户端（应用退出时调用）"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---- 工具方法 ----

    def _detect_exchange(self, symbol: str) -> str:
        return detect_exchange(symbol)

    def _to_full_code(self, symbol: str, exchange: str) -> str:
        return f"{symbol}.{exchange}"

    def _normalize_symbol(self, symbol: str) -> Tuple[str, str]:
        """标准化股票代码"""
        if '.' in symbol:
            parts = symbol.split('.')
            return parts[0], parts[1].upper()
        exchange = self._detect_exchange(symbol)
        return symbol, exchange

    def _to_tencent_code(self, symbol: str, exchange: str) -> str:
        """转换为腾讯股票代码格式: sh600519, sz000001"""
        return f"{exchange.lower()}{symbol}"

    def _parse_tencent_response(
        self, text: str, requested_codes: Optional[List[str]] = None
    ) -> Tuple[List[Dict[str, Any]], Set[str]]:
        """
        解析腾讯批量接口返回的数据

        Returns:
            (成功解析的结果列表, 未能解析的 code 集合)

        格式（多行）:
            v_sh600519="1~贵州茅台~600519~1445.00~1452.87~..."
            v_sz000001="1~平安银行~000001~12.34~12.30~..."
        """
        results: List[Dict[str, Any]] = []
        failed_codes: Set[str] = set()
        parsed_codes: Set[str] = set()

        for line in text.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            # 严格匹配，逐行解析
            m = re.match(r'^v_(sh\d+|sz\d+|bj\d+)="(.*)"', line)
            if not m:
                continue
            code_part = m.group(1)          # e.g. sh600519
            data_str = m.group(2)
            # 处理 "none" 响应行（股票不存在/退市），不整批失败，逐行跳过
            if data_str.lower() == 'none':
                failed_codes.add(code_part)
                continue
            fields = data_str.split('~')
            if len(fields) < 10:
                failed_codes.add(code_part)
                logger.warning("字段不足，跳过: %s", line[:60])
                continue
            # 提取交易所和代码
            exchange_str = code_part[:2]     # sh / sz / bj
            symbol = code_part[2:]
            exchange_map = {'sh': 'SH', 'sz': 'SZ', 'bj': 'BJ'}
            exchange = exchange_map.get(exchange_str, 'SZ')
            try:
                results.append({
                    'symbol': symbol,
                    'exchange': exchange,
                    'full_code': self._to_full_code(symbol, exchange),
                    'name': fields[1],
                    'current_price': float(fields[3]) if fields[3] else 0.0,
                    'prev_close': float(fields[4]) if fields[4] else 0.0,
                    'open': float(fields[5]) if fields[5] else 0.0,
                    'volume': float(fields[6]) if fields[6] else 0.0,
                    'turnover': float(fields[7]) if fields[7] else 0.0,
                })
                parsed_codes.add(code_part)
            except (ValueError, IndexError) as e:
                failed_codes.add(code_part)
                logger.warning("解析字段失败，跳过 %s: %s", code_part, e)
                continue

        # 如果提供了请求列表，但解析结果极少，说明整批可能真正失败了
        if requested_codes and len(results) == 0 and len(failed_codes) == 0:
            logger.warning("腾讯接口返回未解析内容，整批疑似失败: %s", text[:120])

        return results, failed_codes

    def _calculate_change_percent(self, current_price: float, prev_close: float) -> float:
        if prev_close == 0:
            return 0.0
        return ((current_price - prev_close) / prev_close) * 100

    # ---- 限频 + 请求 ----

    async def _acquire_rate_limit(self):
        """全局速率限制（用 Lock 保证不竞态）"""
        async with self._rate_limit_lock:
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < self.GLOBAL_RATE_LIMIT:
                await asyncio.sleep(self.GLOBAL_RATE_LIMIT - elapsed)
            self._last_request_time = time.time()

    async def _fetch_batch(self, tencent_codes: List[str]) -> List[Dict[str, Any]]:
        """
        真正批量请求：多个 code 用逗号分隔，一次请求

        Raises:
            UpstreamTimeout / UpstreamRateLimited / UpstreamInvalidResponse
        """
        await self._acquire_rate_limit()

        codes_param = ','.join(tencent_codes)
        url = f"https://qt.gtimg.cn/q={codes_param}"

        client = await self._get_client()
        try:
            response = await client.get(url)
        except httpx.TimeoutException as e:
            raise UpstreamTimeout(f"腾讯接口超时: {e}") from e
        except httpx.ConnectError as e:
            raise UpstreamConnectionError(f"腾讯接口连接失败: {e}") from e

        if response.status_code == 429:
            raise UpstreamRateLimited("腾讯接口被限流 (429)")
        if response.status_code >= 500:
            raise UpstreamInvalidResponse(f"腾讯接口服务端错误: {response.status_code}")

        response.raise_for_status()
        text = response.text

        if not text.strip():
            raise UpstreamInvalidResponse("腾讯接口返回空数据")

        # 传入请求的 codes，以便解析器追踪哪些 code 解析失败
        return self._parse_tencent_response(text, tencent_codes)

    async def _fetch_single_with_retry(self, symbol: str, exchange: str) -> Optional[StockPrice]:
        """单股获取（带指数退避重试）"""
        last_error: Exception = UpstreamInvalidResponse("unknown")

        for attempt in range(self._max_retries):
            try:
                tencent_code = self._to_tencent_code(symbol, exchange)
                parsed, _ = await self._fetch_batch([tencent_code])
                if not parsed:
                    return None
                data = parsed[0]
                change_percent = self._calculate_change_percent(
                    data['current_price'], data['prev_close']
                )
                return StockPrice(
                    symbol=symbol,
                    exchange=exchange,
                    full_code=data['full_code'],
                    name=data['name'],
                    current_price=data['current_price'],
                    change_percent=change_percent,
                    update_time=time.strftime('%H:%M:%S'),
                )
            except UpstreamConnectionError as e:
                last_error = e
                if attempt < self._max_retries - 1:
                    wait = self._retry_backoff_base * (2 ** attempt)
                    # 加 jitter 避免惊群
                    wait += random.uniform(0, 0.3)
                    logger.warning(
                        "获取 %s.%s 连接失败 (attempt %d/%d), %.1f秒后重试: %s",
                        symbol, exchange, attempt + 1, self._max_retries, wait, e
                    )
                    await asyncio.sleep(wait)
            except (UpstreamTimeout, UpstreamRateLimited) as e:
                last_error = e
                if attempt < self._max_retries - 1:
                    wait = self._retry_backoff_base * (2 ** attempt)
                    wait += random.uniform(0, 0.3)
                    logger.warning(
                        "获取 %s.%s 失败 (attempt %d/%d), %.1f秒后重试: %s",
                        symbol, exchange, attempt + 1, self._max_retries, wait, e
                    )
                    await asyncio.sleep(wait)
            except UpstreamInvalidResponse:
                # 无效响应不重试
                logger.warning("获取 %s.%s 返回无效响应，不重试", symbol, exchange)
                return None
            except Exception as e:
                last_error = e
                logger.error("获取 %s.%s 异常: %s", symbol, exchange, e)
                return None

        logger.error("获取 %s.%s 达到最大重试次数: %s", symbol, exchange, last_error)
        return None

    # ---- 公开 API ----

    async def get_price(self, symbol: str) -> Optional[StockPrice]:
        """获取单只股票价格"""
        symbol, exchange = self._normalize_symbol(symbol)
        full_code = self._to_full_code(symbol, exchange)

        # 检查 per-symbol TTL 缓存
        entry = self._cache.get(full_code)
        if entry is not None and time.time() - entry.timestamp < self.SYMBOL_CACHE_TTL:
            return entry.data

        price = await self._fetch_single_with_retry(symbol, exchange)
        if price is not None:
            self._cache[full_code] = _CacheEntry(data=price, timestamp=time.time())
        return price

    async def get_prices_batch(self, symbols: List[str]) -> Dict[str, StockPrice]:
        """
        批量获取股票价格（使用腾讯批量接口）

        Args:
            symbols: 股票代码列表，如 ["000001", "000002.SZ"]

        Returns:
            Dict[full_code, StockPrice]
        """
        if not symbols:
            return {}

        # 限制总量
        if len(symbols) > 200:
            raise ValueError(f"Batch size too large: {len(symbols)}, max 200")

        # 标准化
        normalized: List[Tuple[str, str, str]] = []
        for symbol in symbols:
            try:
                s, e = self._normalize_symbol(symbol)
                normalized.append((s, e, self._to_full_code(s, e)))
            except ValueError:
                continue

        result: Dict[str, StockPrice] = {}
        failed_symbols: List[Tuple[str, str, str]] = []

        # 按 BATCH_MAX_CODES 分批请求腾讯批量接口
        for i in range(0, len(normalized), self.BATCH_MAX_CODES):
            batch = normalized[i:i + self.BATCH_MAX_CODES]

            # 先从缓存命中
            cached, need_fetch = [], []
            for s, e, fc in batch:
                entry = self._cache.get(fc)
                if entry is not None and time.time() - entry.timestamp < self.SYMBOL_CACHE_TTL:
                    result[fc] = entry.data
                    cached.append(fc)
                else:
                    need_fetch.append((s, e, fc))

            if not need_fetch:
                continue

            # 构造腾讯批量 code 列表
            tencent_codes = [self._to_tencent_code(s, e) for s, e, _ in need_fetch]

            try:
                parsed, _ = await self._fetch_batch(tencent_codes)
                # 建立 code -> normalized index 的映射
                tencent_to_norm = {
                    self._to_tencent_code(s, e): fc
                    for s, e, fc in need_fetch
                }
                success_codes: Set[str] = set()
                for data in parsed:
                    tencent_code = self._to_tencent_code(data['symbol'], data['exchange'])
                    key = tencent_to_norm.get(tencent_code)
                    if key is None:
                        continue
                    change_percent = self._calculate_change_percent(
                        data['current_price'], data['prev_close']
                    )
                    price = StockPrice(
                        symbol=data['symbol'],
                        exchange=data['exchange'],
                        full_code=data['full_code'],
                        name=data['name'],
                        current_price=data['current_price'],
                        change_percent=change_percent,
                        update_time=time.strftime('%H:%M:%S'),
                    )
                    result[key] = price
                    success_codes.add(tencent_code)
                    self._cache[key] = _CacheEntry(data=price, timestamp=time.time())

                # 差集补偿：请求了但未解析出的 symbol，降级逐个重试
                for s, e, fc in need_fetch:
                    tc = self._to_tencent_code(s, e)
                    if tc not in success_codes:
                        failed_symbols.append((s, e, fc))
            except (UpstreamTimeout, UpstreamRateLimited, UpstreamInvalidResponse, UpstreamConnectionError) as e:
                logger.warning(
                    "批量请求第 %d 批失败 (codes=%s): %s",
                    i // self.BATCH_MAX_CODES + 1,
                    tencent_codes[:3],
                    e,
                )
                failed_symbols.extend(need_fetch)

        # 失败项降级为逐个重试
        for s, e, fc in failed_symbols:
            price = await self._fetch_single_with_retry(s, e)
            if price is not None:
                result[fc] = price
                self._cache[fc] = _CacheEntry(data=price, timestamp=time.time())

        return result

    def clear_cache(self):
        """清除缓存"""
        self._cache.clear()


# 全局客户端实例
akshare_client = AKShareClient()
