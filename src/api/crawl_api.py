"""
爬虫任务 API
支持手动回补、任务状态查询
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..database import AsyncSessionLocal
from ..database.models import EmailLog
from ..services.crawl_scheduler_service import crawl_scheduler_service, CrawlJobStatus

router = APIRouter(prefix="/api/crawl", tags=["crawl"])


# ============================
# 请求/响应模型
# ============================

class BackfillRequest(BaseModel):
    """手动回补请求"""
    start_date: str = Field(..., description="起始日期 YYYY-MM-DD")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD")
    force_reparse: bool = Field(default=False, description="是否强制重新解析已解析过的公告")


class JobStatusResponse(BaseModel):
    """任务状态响应"""
    job_id: str
    job_type: str
    start_date: Optional[str]
    end_date: Optional[str]
    status: str
    total_fetched: int
    new_added: int
    parse_success: int
    parse_failed: int
    pdf_download_success: int
    pdf_download_skipped: int
    pdf_download_failed: int
    watch_targets_created: int
    message: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]


class BackfillSubmitResponse(BaseModel):
    """回补任务提交响应"""
    success: bool
    job_id: str
    message: str


class EmailLogResponse(BaseModel):
    """邮件发送日志响应"""
    id: int
    notification_type: str
    status: str
    job_id: Optional[str]
    crawl_date: Optional[str]
    stock_symbol: Optional[str]
    subject: str
    recipients: Optional[str]
    error_message: Optional[str]
    created_at: Optional[str]


class ScheduleStatusResponse(BaseModel):
    """定时任务状态响应"""
    job_id: str
    cron_hours: str
    timezone: str
    is_running: bool
    registered: bool
    next_run_time: Optional[str]


# ============================
# 日期校验
# ============================

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_date(date_str: str, field_name: str) -> None:
    if not DATE_RE.match(date_str):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} 格式错误，应为 YYYY-MM-DD，实际：{date_str}",
        )
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} 日期无效：{date_str}",
        )


# ============================
# API 端点
# ============================

@router.post("/backfill", response_model=BackfillSubmitResponse)
async def submit_backfill(request: BackfillRequest):
    """
    提交手动回补任务

    按指定日期范围从巨潮资讯网抓取股权激励草案公告
    """
    # 参数校验
    validate_date(request.start_date, "start_date")
    validate_date(request.end_date, "end_date")

    start_dt = datetime.strptime(request.start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(request.end_date, "%Y-%m-%d")

    if start_dt > end_dt:
        raise HTTPException(status_code=400, detail="start_date 不能晚于 end_date")

    # 限制回补范围不超过1年
    if (end_dt - start_dt).days > 365:
        raise HTTPException(
            status_code=400,
            detail="回补范围不能超过1年（365天）",
        )

    try:
        job_id = await crawl_scheduler_service.submit_backfill(
            start_date=request.start_date,
            end_date=request.end_date,
            force_reparse=request.force_reparse,
        )
        return BackfillSubmitResponse(
            success=True,
            job_id=job_id,
            message=f"回补任务已提交 ({request.start_date} ~ {request.end_date})",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"任务提交失败: {e}")


@router.get("/runs", response_model=list[JobStatusResponse])
async def list_recent_runs(limit: int = Query(default=10, ge=1, le=100)):
    """
    查询最近爬取任务执行记录
    """
    runs = await crawl_scheduler_service.get_recent_runs(limit=limit)
    return [
        JobStatusResponse(
            job_id=r.job_id,
            job_type=r.job_type,
            start_date=r.start_date,
            end_date=r.end_date,
            status=r.status,
            total_fetched=r.total_fetched or 0,
            new_added=r.new_added or 0,
            parse_success=r.parse_success or 0,
            parse_failed=r.parse_failed or 0,
            pdf_download_success=r.pdf_download_success or 0,
            pdf_download_skipped=r.pdf_download_skipped or 0,
            pdf_download_failed=r.pdf_download_failed or 0,
            watch_targets_created=r.watch_targets_created or 0,
            message=r.message,
            started_at=r.started_at.isoformat() if r.started_at else None,
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
        )
        for r in runs
    ]


@router.get("/runs/{job_id}", response_model=JobStatusResponse)
async def get_run_status(job_id: str):
    """
    查询指定任务的执行状态
    """
    runs = await crawl_scheduler_service.get_recent_runs(limit=100)
    for r in runs:
        if r.job_id == job_id:
            return JobStatusResponse(
                job_id=r.job_id,
                job_type=r.job_type,
                start_date=r.start_date,
                end_date=r.end_date,
                status=r.status,
                total_fetched=r.total_fetched or 0,
                new_added=r.new_added or 0,
                parse_success=r.parse_success or 0,
                parse_failed=r.parse_failed or 0,
                pdf_download_success=r.pdf_download_success or 0,
                pdf_download_skipped=r.pdf_download_skipped or 0,
                pdf_download_failed=r.pdf_download_failed or 0,
                watch_targets_created=r.watch_targets_created or 0,
                message=r.message,
                started_at=r.started_at.isoformat() if r.started_at else None,
                completed_at=r.completed_at.isoformat() if r.completed_at else None,
            )
    raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")


@router.get("/email-logs", response_model=list[EmailLogResponse])
async def list_recent_email_logs(limit: int = Query(default=20, ge=1, le=200)):
    """
    查询最近邮件发送记录
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EmailLog).order_by(EmailLog.created_at.desc()).limit(limit)
        )
        logs = result.scalars().all()

    return [
        EmailLogResponse(
            id=log.id,
            notification_type=log.notification_type,
            status=log.status,
            job_id=log.job_id,
            crawl_date=log.crawl_date,
            stock_symbol=log.stock_symbol,
            subject=log.subject,
            recipients=log.recipients,
            error_message=log.error_message,
            created_at=log.created_at.isoformat() if log.created_at else None,
        )
        for log in logs
    ]


@router.get("/schedule", response_model=ScheduleStatusResponse)
async def get_schedule_status():
    """
    查询当前定时爬虫配置与注册状态
    """
    return ScheduleStatusResponse(**crawl_scheduler_service.get_schedule_status())
