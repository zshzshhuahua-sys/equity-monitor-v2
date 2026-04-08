"""
巨潮资讯网异步爬虫客户端
基于httpx.AsyncClient，支持并发请求和自动重试
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx

from ..config.settings import CNINFO_ANNOUNCEMENT_URL, DEFAULT_HEADERS
from ..services.announcement_rule_engine import AnnouncementRaw

logger = logging.getLogger(__name__)

# 重试配置
MAX_RETRIES = 3
RETRY_DELAY = 2.0


class CNInfoClient:
    """巨潮资讯网异步API客户端"""

    def __init__(self):
        self.base_url = "http://www.cninfo.com.cn"
        self.search_url = CNINFO_ANNOUNCEMENT_URL
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=DEFAULT_HEADERS,
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _post_with_retry(self, data: dict) -> dict:
        """POST请求，带重试（HTTP错误 + 空结果重试）"""
        client = await self._get_client()
        for attempt in range(MAX_RETRIES):
            try:
                response = await client.post(self.search_url, data=data)
                response.raise_for_status()
                result = response.json()
                # 服务器间歇性返回 announcements=null（即使 totalAnnouncement>0），发现空列表时重试
                ann_list = result.get("announcements")
                if ann_list is None or (isinstance(ann_list, list) and len(ann_list) == 0):
                    logger.warning(
                        "第 %s 次请求返回空（服务器间歇性），重试中...",
                        attempt + 1,
                    )
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(2.0 * (attempt + 1))
                        continue
                return result
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                logger.warning(
                    "请求失败 (attempt %s/%s): %s",
                    attempt + 1, MAX_RETRIES, e,
                )
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    raise

    def _build_search_params(
        self,
        page_num: int,
        page_size: int,
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> dict:
        """构造搜索参数"""
        se_date = ""
        if start_date:
            if end_date:
                se_date = f"{start_date}~{end_date}"
            else:
                today = datetime.now().strftime("%Y-%m-%d")
                se_date = f"{start_date}~{today}"

        return {
            "pageNum": page_num,
            "pageSize": page_size,
            "tabName": "fulltext",
            "column": "sse",
            "stock": "",
            "searchkey": "股权激励草案",
            "secid": "",
            "plate": "",
            "category": "category_equity_incentive",
            "trade": "",
            "columnTitle": "历年股权激励",
            "sortName": "",
            "sortType": "",
            "limit": "",
            "showTitle": "",
            "seDate": se_date,
        }

    async def fetch_page(
        self,
        page_num: int = 1,
        page_size: int = 30,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """
        获取单页公告

        Returns:
            API响应的JSON数据（含 totalRecords, announcements 列表）
        """
        params = self._build_search_params(page_num, page_size, start_date, end_date)
        logger.info(
            "抓取公告页: page=%s seDate=%s",
            page_num, params["seDate"] or "全部",
        )
        return await self._post_with_retry(params)

    async def fetch_all_pages(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_pages: Optional[int] = None,
    ) -> list[AnnouncementRaw]:
        """
        翻页抓取所有公告，返回 AnnouncementRaw 列表

        Args:
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD
            max_pages: 最大页数限制，None表示不限制
        """
        all_raw: list[AnnouncementRaw] = []
        page_num = 1

        while True:
            try:
                data = await self.fetch_page(
                    page_num=page_num,
                    start_date=start_date,
                    end_date=end_date,
                )
            except Exception as e:
                logger.error("抓取第 %s 页失败: %s", page_num, e)
                break

            announcements = data.get("announcements", [])
            if not announcements:
                logger.info("第 %s 页无数据，停止翻页", page_num)
                break

            for item in announcements:
                raw = self._parse_item(item)
                if raw.stock_code:
                    all_raw.append(raw)

            total_records = data.get("totalRecords", 0)
            logger.info(
                "已获取 %s/%s 条公告",
                len(all_raw), total_records,
            )

            if len(announcements) < 30:
                break
            if max_pages and page_num >= max_pages:
                break

            page_num += 1
            await asyncio.sleep(1.0)

        logger.info("抓取完成，共 %s 条公告", len(all_raw))
        return all_raw

    def _parse_item(self, item: dict) -> AnnouncementRaw:
        """将API返回的item解析为 AnnouncementRaw"""
        ann_time = item.get("announcementTime")
        if ann_time:
            if isinstance(ann_time, int):
                publish_date = datetime.fromtimestamp(ann_time / 1000).strftime("%Y-%m-%d")
            else:
                publish_date = str(ann_time).split()[0][:10] if " " in str(ann_time) else str(ann_time)[:10]
        else:
            publish_date = ""

        return AnnouncementRaw(
            announcement_id=item.get("announcementId", ""),
            stock_code=(item.get("secCode") or "").strip(),
            stock_name=(item.get("secName") or "").strip(),
            exchange="",
            title=(item.get("announcementTitle") or "").strip(),
            publish_date=publish_date,
            announcement_time=ann_time if isinstance(ann_time, int) else None,
            pdf_url=item.get("adjunctUrl") or "",
            adjunct_url=item.get("adjunctUrl") or "",
        )
