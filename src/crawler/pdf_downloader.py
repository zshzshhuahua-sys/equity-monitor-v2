"""
异步PDF下载器
基于httpx.AsyncClient，支持并发下载和自动重试
"""
import asyncio
import logging
from pathlib import Path
from urllib.parse import urljoin

import httpx

from ..config.settings import CNINFO_BASE_URL, DEFAULT_HEADERS

logger = logging.getLogger(__name__)

PDF_BASE_URL = "http://static.cninfo.com.cn/"
MAX_RETRIES = 3
RETRY_DELAY = 2.0
CHUNK_SIZE = 8192


class AsyncPDFDownloader:
    """异步PDF文件下载器"""

    def __init__(self, download_dir: Path | str | None = None):
        if download_dir is None:
            download_dir = Path(__file__).parent.parent.parent / "data" / "pdfs"
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = dict(DEFAULT_HEADERS)
            headers["Accept"] = "application/pdf,application/octet-stream,*/*"
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(60.0, connect=15.0),
                follow_redirects=True,
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    def _build_filename(
        self,
        stock_code: str,
        publish_date: str,
        title: str,
        announcement_id: str = "",
    ) -> str:
        clean_title = "".join(
            c for c in title if c.isalnum() or c in (" ", "-", "_")
        ).strip()[:20]
        ann_id_short = announcement_id[:8] if announcement_id else ""
        return f"{stock_code}_{publish_date}_{ann_id_short}_{clean_title}"

    def _get_full_url(self, relative_url: str) -> str:
        if relative_url.startswith("http"):
            return relative_url
        return urljoin(PDF_BASE_URL, relative_url)

    async def download_one(
        self,
        pdf_url: str,
        stock_code: str,
        publish_date: str,
        title: str,
        announcement_id: str = "",
        force: bool = False,
    ) -> str | None:
        """
        下载单个PDF

        Returns:
            本地文件路径，失败返回None
        """
        filename_base = self._build_filename(
            stock_code, publish_date, title, announcement_id
        )
        local_path = self.download_dir / f"{filename_base}.pdf"

        if local_path.exists() and not force:
            logger.info("PDF已存在，跳过: %s", local_path.name)
            return str(local_path)

        full_url = self._get_full_url(pdf_url)
        client = await self._get_client()

        for attempt in range(MAX_RETRIES):
            try:
                async with client.stream("GET", full_url) as response:
                    response.raise_for_status()
                    with open(local_path, "wb") as f:
                        async for chunk in response.aiter_bytes(CHUNK_SIZE):
                            f.write(chunk)
                logger.info("下载完成: %s", local_path.name)
                return str(local_path)
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                logger.warning(
                    "下载失败 (attempt %s/%s) %s: %s",
                    attempt + 1, MAX_RETRIES, stock_code, e,
                )
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    if local_path.exists():
                        local_path.unlink()
                    return None
            except Exception as e:
                logger.error("保存PDF失败 %s: %s", stock_code, e)
                if local_path.exists():
                    local_path.unlink()
                return None

        return None

    async def download_batch(
        self,
        items: list[dict],
        force: bool = False,
        concurrency: int = 3,
    ) -> dict[str, list]:
        """
        并发批量下载PDF

        Args:
            items: [{"pdf_url", "stock_code", "publish_date", "title"}, ...]
            force: 是否强制重新下载
            concurrency: 并发数

        Returns:
            {"success": [路径], "failed": [stock_code], "skipped": [路径]}
        """
        results: dict[str, list] = {"success": [], "failed": [], "skipped": []}
        total = len(items)

        semaphore = asyncio.Semaphore(concurrency)

        async def download_one_item(idx: int, item: dict):
            async with semaphore:
                stock_code = item.get("stock_code", "")
                pdf_url = item.get("pdf_url", "")
                publish_date = item.get("publish_date", "")
                title = item.get("title", "")
                announcement_id = item.get("announcement_id", "")

                if not pdf_url:
                    logger.warning("[%s/%s] %s 无PDF链接", idx + 1, total, stock_code)
                    results["failed"].append(stock_code)
                    return

                # 检查是否已存在
                filename_base = self._build_filename(
                    stock_code, publish_date, title, announcement_id
                )
                local_path = self.download_dir / f"{filename_base}.pdf"
                if local_path.exists() and not force:
                    logger.info("[%s/%s] %s 已存在，跳过", idx + 1, total, stock_code)
                    results["skipped"].append(str(local_path))
                    return

                path = await self.download_one(
                    pdf_url=pdf_url,
                    stock_code=stock_code,
                    publish_date=publish_date,
                    title=title,
                    announcement_id=announcement_id,
                    force=force,
                )
                if path:
                    results["success"].append(path)
                else:
                    results["failed"].append(stock_code)

                if idx % 10 == 0 or idx == total - 1:
                    logger.info("下载进度: %s/%s", idx + 1, total)

        tasks = [
            download_one_item(i, item)
            for i, item in enumerate(items)
        ]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results_list):
            if isinstance(result, Exception):
                item = items[i]
                logger.error("下载任务异常 %s (%s): %s",
                             item.get("stock_code", "?"),
                             item.get("announcement_id", "?"),
                             result)
                results["failed"].append(item.get("stock_code", "?"))

        logger.info(
            "批量下载完成: 成功 %s, 跳过 %s, 失败 %s",
            len(results["success"]),
            len(results["skipped"]),
            len(results["failed"]),
        )
        return results
