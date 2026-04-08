"""
爬虫调度服务
支持：
  - 定时增量任务（每天10:00、22:00北京时间）
  - 手动回补任务（按日期范围）
  - 任务状态追踪
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, date
from enum import Enum
from typing import Optional, List

import pdfplumber

from sqlalchemy import select, func
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

from ..database import AsyncSessionLocal
from ..database.models import Announcement, CrawlLog
from ..notifiers.email import email_notifier

logger = logging.getLogger(__name__)


class CrawlJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class CrawlSchedulerService:
    """爬虫调度服务"""

    JOB_ID_SCHEDULED = "scheduled_crawl"
    JOB_ID_PREFIX = "backfill_crawl"
    SCHEDULE_HOURS = "10,22"
    SCHEDULE_TIMEZONE = "Asia/Shanghai"

    def __init__(self):
        self.scheduler: Optional[AsyncIOScheduler] = None
        self._is_running = False
        self._job_lock: Optional[asyncio.Lock] = None

    # ---- 生命周期 ----

    def _ensure_job_lock(self) -> asyncio.Lock:
        """确保任务锁已初始化，便于手动触发执行。"""
        if self._job_lock is None:
            self._job_lock = asyncio.Lock()
        return self._job_lock

    def start(self):
        """启动调度器，注册定时任务"""
        if self._is_running:
            logger.warning("爬虫调度器已在运行")
            return

        self._ensure_job_lock()

        try:
            self.scheduler = AsyncIOScheduler()
            self.scheduler.add_listener(
                self._job_listener,
                EVENT_JOB_EXECUTED | EVENT_JOB_ERROR,
            )
            self._add_scheduled_job()
            self.scheduler.start()
            self._is_running = True
            logger.info("爬虫调度器已启动（定时任务 10:00、22:00 北京时间）")
        except Exception as e:
            logger.exception("爬虫调度器启动失败: %s", e)

    def stop(self):
        """停止调度器"""
        if not self._is_running:
            return
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None
        self._is_running = False
        logger.info("爬虫调度器已停止")

    # ---- 定时任务注册 ----

    def _add_scheduled_job(self):
        """添加每日两次增量爬取任务"""
        trigger = CronTrigger(
            hour=self.SCHEDULE_HOURS,
            minute=0,
            timezone=self.SCHEDULE_TIMEZONE,
        )
        self.scheduler.add_job(
            func=self._run_incremental,
            trigger=trigger,
            id=self.JOB_ID_SCHEDULED,
            replace_existing=True,
            max_instances=1,
        )
        logger.info("已注册定时增量爬取任务（每天 10:00、22:00 北京时间）")

    async def submit_backfill(
        self,
        start_date: str,
        end_date: str,
        force_reparse: bool = False,
    ) -> str:
        """
        提交手动回补任务

        Returns:
            job_id（与执行/落库使用同一ID）
        """
        job_id = f"{self.JOB_ID_PREFIX}_{start_date}_{end_date}"

        async with self._ensure_job_lock():
            self.scheduler.add_job(
                func=self._run_backfill,
                trigger="date",
                id=job_id,
                kwargs={
                    "job_id": job_id,
                    "start_date": start_date,
                    "end_date": end_date,
                    "force_reparse": force_reparse,
                },
                replace_existing=True,
            )

        logger.info("已提交回补任务: %s (%s ~ %s)", job_id, start_date, end_date)
        return job_id

    # ---- 核心执行逻辑 ----

    async def run_scheduled_once(self):
        """手动执行一次定时增量爬取，用于联调验证。"""
        await self._run_incremental()

    async def _run_incremental(self):
        """定时增量爬取"""
        async with self._ensure_job_lock():
            job_id = self.JOB_ID_SCHEDULED
            started_at = datetime.utcnow()

            # 查询最新公告日期作为起始日期
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(func.max(Announcement.publish_date)).where(
                        Announcement.is_eligible == True
                    )
                )
                max_date = result.scalar_one_or_none()

            if max_date:
                # 从 max_date + 1 天开始
                from datetime import datetime as dt
                latest_dt = dt.strptime(max_date, "%Y-%m-%d")
                start_date = (latest_dt + timedelta(days=1)).strftime("%Y-%m-%d")
                logger.info("增量爬取起始日期: %s", start_date)
            else:
                # 数据库为空，获取最近7天
                start_date = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
                logger.info("数据库为空，从最近7天开始: %s", start_date)

            end_date = datetime.utcnow().strftime("%Y-%m-%d")

            try:
                stats = await self._execute_crawl(job_id, start_date, end_date)
                await self._log_crawl_run(
                    job_id=job_id,
                    job_type="incremental",
                    start_date=start_date,
                    end_date=end_date,
                    status=CrawlJobStatus.SUCCESS.value,
                    stats=stats,
                    started_at=started_at,
                )
                logger.info("定时增量爬取完成: %s", stats)
            except Exception as e:
                logger.exception("定时增量爬取失败: %s", e)
                await self._log_crawl_run(
                    job_id=job_id,
                    job_type="incremental",
                    start_date=start_date,
                    end_date=end_date,
                    status=CrawlJobStatus.FAILED.value,
                    message=str(e),
                    started_at=started_at,
                )
                return

            # 邮件发送独立处理，失败不影响爬取结果
            try:
                crawl_date = datetime.strptime(end_date, "%Y-%m-%d").date()
                new_anns = await self._get_new_announcements(crawl_date)
                await email_notifier.send_crawl_report_async(
                    crawl_date,
                    stats,
                    new_anns,
                    job_id=job_id,
                )
            except Exception as e:
                logger.warning("邮件报告发送失败，不影响爬取结果: %s", e)

    async def _run_backfill(
        self,
        job_id: str,
        start_date: str,
        end_date: str,
        force_reparse: bool = False,
    ):
        """手动回补爬取"""
        started_at = datetime.utcnow()

        try:
            stats = await self._execute_crawl(job_id, start_date, end_date, force_reparse)
            await self._log_crawl_run(
                job_id=job_id,
                job_type="backfill",
                start_date=start_date,
                end_date=end_date,
                status=CrawlJobStatus.SUCCESS.value,
                stats=stats,
                started_at=started_at,
            )
            logger.info("回补爬取完成: %s", stats)
        except Exception as e:
            logger.exception("回补爬取失败: %s", e)
            await self._log_crawl_run(
                job_id=job_id,
                job_type="backfill",
                start_date=start_date,
                end_date=end_date,
                status=CrawlJobStatus.FAILED.value,
                message=str(e),
                started_at=started_at,
            )

    async def _execute_crawl(
        self,
        job_id: str,
        start_date: str,
        end_date: str,
        force_reparse: bool = False,
    ) -> dict:
        """
        执行爬取：抓取公告 → 下载PDF → 提取字段 → 入库
        返回统计信息
        """
        from ..crawler.cninfo_client import CNInfoClient
        from ..crawler.pdf_downloader import AsyncPDFDownloader
        from ..parser.field_extractors import extract_fields_from_text
        from ..services.announcement_ingest_service import announcement_ingest_service

        logger.info(
            "[%s] 开始执行爬取: start_date=%s end_date=%s",
            job_id, start_date, end_date,
        )

        cninfo = CNInfoClient()
        downloader = AsyncPDFDownloader()

        try:
            # 1. 抓取所有公告页
            raw_list = await cninfo.fetch_all_pages(
                start_date=start_date,
                end_date=end_date,
            )
            total_fetched = len(raw_list)
            logger.info("[%s] 抓取到 %s 条公告", job_id, total_fetched)

            if not raw_list:
                return {
                    "total_fetched": 0,
                    "new_added": 0,
                    "pdf_download_success": 0,
                    "pdf_download_skipped": 0,
                    "pdf_download_failed": 0,
                    "parse_success": 0,
                    "parse_failed": 0,
                    "watch_targets_created": 0,
                }

            # 2. 收集需要下载PDF的条目（需有PDF链接）
            pdf_items = [
                {
                    "stock_code": r.stock_code,
                    "publish_date": r.publish_date,
                    "title": r.title,
                    "announcement_id": r.announcement_id,
                    "pdf_url": r.pdf_url or r.adjunct_url,
                }
                for r in raw_list
                if r.pdf_url or r.adjunct_url
            ]
            logger.info("[%s] 需要下载PDF: %s 条", job_id, len(pdf_items))

            # 3. 批量下载PDF（跳过已存在的，除非 force_reparse）
            download_results = await downloader.download_batch(
                items=pdf_items,
                force=force_reparse,
                concurrency=3,
            )
            logger.info("[%s] PDF下载完成: 成功=%s 跳过=%s 失败=%s",
                        job_id, len(download_results["success"]),
                        len(download_results["skipped"]),
                        len(download_results["failed"]))

            # 4. 逐条入库（复用 announcement_ingest_service）
            new_added = 0
            parse_success = 0
            parse_failed = 0
            watch_targets_created = 0

            for r in raw_list:
                pdf_path: str | None = None

                # 查找本地PDF路径（只认实际下载成功的文件）
                if r.pdf_url or r.adjunct_url:
                    filename_base = downloader._build_filename(
                        r.stock_code, r.publish_date, r.title, r.announcement_id
                    )
                    candidate = downloader.download_dir / f"{filename_base}.pdf"
                    if candidate.exists():
                        pdf_path = str(candidate)

                # 从PDF提取字段
                strike_price: float | None = None
                option_ratio: float | None = None
                incentive_object_count: int | None = None
                option_allocation = ""
                performance_requirements = ""
                parse_status = "pending"

                if pdf_path:
                    try:
                        text = await asyncio.to_thread(_extract_pdf_text, pdf_path)
                        if text:
                            fields = extract_fields_from_text(text)
                            strike_price = fields.exercise_price
                            option_ratio = fields.option_ratio
                            incentive_object_count = fields.incentive_object_count
                            option_allocation = fields.option_allocation
                            performance_requirements = fields.performance_requirements
                            parse_status = "success"
                            parse_success += 1
                        else:
                            parse_status = "failed"
                            parse_failed += 1
                    except Exception as e:
                        logger.warning("PDF解析失败 %s: %s", r.stock_code, e)
                        parse_status = "failed"
                        parse_failed += 1

                # 入库
                result = await announcement_ingest_service.ingest_announcement(
                    raw=r,
                    strike_price=strike_price,
                    option_ratio=option_ratio,
                    incentive_object_count=incentive_object_count,
                    option_allocation=option_allocation,
                    performance_requirements=performance_requirements,
                    parse_status=parse_status,
                    pdf_path=pdf_path,
                    force_reparse=force_reparse,
                )

                if result.action in ("inserted", "updated"):
                    new_added += 1
                if result.watch_target_updated:
                    watch_targets_created += 1

            return {
                "total_fetched": total_fetched,
                "new_added": new_added,
                "pdf_download_success": len(download_results["success"]),
                "pdf_download_skipped": len(download_results["skipped"]),
                "pdf_download_failed": len(download_results["failed"]),
                "parse_success": parse_success,
                "parse_failed": parse_failed,
                "watch_targets_created": watch_targets_created,
            }

        finally:
            await cninfo.close()
            await downloader.close()

    # ---- 日志记录 ----

    async def _get_new_announcements(self, crawl_date: date) -> List[dict]:
        """获取指定日期新入库的公告（用于邮件报告）"""
        date_str = crawl_date.strftime("%Y-%m-%d")
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Announcement).where(
                    Announcement.publish_date == date_str,
                    Announcement.is_latest_of_day == True,
                ).order_by(Announcement.announcement_time.desc())
            )
            anns = result.scalars().all()
            return [
                {
                    "stock_code": a.stock_code,
                    "stock_name": a.stock_name,
                    "publish_date": a.publish_date,
                    "title": a.title,
                    "is_eligible": a.is_eligible,
                    "strike_price": a.strike_price,
                    "option_ratio": a.option_ratio,
                    "incentive_object_count": a.incentive_object_count,
                    "option_allocation": a.option_allocation or "",
                }
                for a in anns
            ]

    async def _log_crawl_run(
        self,
        job_id: str,
        job_type: str,
        start_date: str,
        end_date: str,
        status: str,
        stats: Optional[dict] = None,
        message: str = "",
        started_at: Optional[datetime] = None,
    ):
        """记录爬取运行日志到 CrawlLog 表"""
        async with AsyncSessionLocal() as session:
            try:
                log = CrawlLog(
                    job_id=job_id,
                    job_type=job_type,
                    start_date=start_date,
                    end_date=end_date,
                    status=status,
                    total_fetched=stats.get("total_fetched", 0) if stats else 0,
                    new_added=stats.get("new_added", 0) if stats else 0,
                    parse_success=stats.get("parse_success", 0) if stats else 0,
                    parse_failed=stats.get("parse_failed", 0) if stats else 0,
                    pdf_download_success=stats.get("pdf_download_success", 0) if stats else 0,
                    pdf_download_skipped=stats.get("pdf_download_skipped", 0) if stats else 0,
                    pdf_download_failed=stats.get("pdf_download_failed", 0) if stats else 0,
                    watch_targets_created=stats.get("watch_targets_created", 0) if stats else 0,
                    message=message,
                    started_at=started_at or datetime.utcnow(),
                    completed_at=datetime.utcnow(),
                )
                session.add(log)
                await session.commit()
            except Exception as e:
                logger.error("记录爬取日志失败: %s", e)

    # ---- 事件监听 ----

    def _job_listener(self, event):
        """任务执行监听器"""
        if event.exception:
            logger.error("爬虫任务执行出错: %s", event.job_id)
        else:
            logger.info("爬虫任务执行成功: %s", event.job_id)

    # ---- 状态查询 ----

    async def get_recent_runs(self, limit: int = 10) -> List[CrawlLog]:
        """获取最近爬取记录"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(CrawlLog)
                .order_by(CrawlLog.started_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    def get_schedule_status(self) -> dict:
        """返回当前调度配置及注册状态，便于运行态确认。"""
        job = self.scheduler.get_job(self.JOB_ID_SCHEDULED) if self.scheduler else None
        next_run_time = None
        if job is not None and getattr(job, "next_run_time", None) is not None:
            next_run_time = job.next_run_time.isoformat()

        return {
            "job_id": self.JOB_ID_SCHEDULED,
            "cron_hours": self.SCHEDULE_HOURS,
            "timezone": self.SCHEDULE_TIMEZONE,
            "is_running": self._is_running,
            "registered": job is not None,
            "next_run_time": next_run_time,
        }


# ============================
# 模块级辅助函数
# ============================

def _extract_pdf_text(pdf_path: str) -> str:
    """从PDF提取纯文本（同步函数，使用pdfplumber）"""
    from pathlib import Path

    path = Path(pdf_path)
    if not path.exists():
        return ""

    try:
        text_parts = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages[:10]:  # 最多取前10页
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        return "\n".join(text_parts)
    except Exception as e:
        logger.warning("PDF文本提取失败 %s: %s", pdf_path, e)
        return ""


# 全局实例
crawl_scheduler_service = CrawlSchedulerService()
