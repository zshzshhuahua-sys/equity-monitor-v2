"""
数据库模型定义
支持A股格式：6位数字代码 + 交易所标识
简化版本 - 移除复杂关系，避免SQLAlchemy关系问题
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, 
    ForeignKey, Index
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class StockWatch(Base):
    """监控股票表"""
    __tablename__ = "stock_watch"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(6), nullable=False, comment="股票代码，6位数字")
    exchange = Column(String(2), nullable=False, comment="交易所：SH/SZ/BJ")
    full_code = Column(String(9), nullable=False, comment="完整代码，如000001.SZ")
    name = Column(String(20), nullable=True, comment="股票名称")
    strike_price = Column(Float, nullable=False, comment="执行价格")
    quantity = Column(Integer, nullable=True, comment="持有数量")
    custom_threshold = Column(Float, nullable=True, comment="自定义预警阈值，如0.15表示15%")
    notes = Column(String(500), nullable=True, comment="备注信息")
    is_active = Column(Boolean, default=True, comment="是否启用监控")
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment="更新时间")
    
    # 索引
    __table_args__ = (
        Index("idx_stock_symbol_exchange", "symbol", "exchange", unique=True),
        Index("idx_stock_is_active", "is_active"),
        Index("idx_stock_created_at", "created_at"),
    )
    
    def __repr__(self):
        return f"<StockWatch({self.full_code}, strike={self.strike_price})>"


class PriceCache(Base):
    """价格缓存表"""
    __tablename__ = "price_cache"
    
    symbol = Column(String(6), primary_key=True, comment="股票代码")
    exchange = Column(String(2), nullable=False, comment="交易所")
    full_code = Column(String(9), nullable=False, comment="完整代码")
    last_price = Column(Float, nullable=False, comment="最新价格")
    change_percent = Column(Float, nullable=True, comment="涨跌幅%")
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment="最后更新时间")
    
    # 索引
    __table_args__ = (
        Index("idx_price_last_updated", "last_updated"),
    )
    
    def __repr__(self):
        return f"<PriceCache({self.full_code}, price={self.last_price})>"


class AlertLog(Base):
    """预警记录表"""
    __tablename__ = "alert_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_id = Column(Integer, ForeignKey("stock_watch.id"), nullable=False, comment="关联股票ID")
    alert_type = Column(String(20), nullable=False, comment="预警类型：threshold_breach/price_drop/price_spike")
    threshold_value = Column(Float, nullable=False, comment="触发阈值")
    trigger_price = Column(Float, nullable=False, comment="触发时价格")
    price_diff_percent = Column(Float, nullable=False, comment="价差百分比")
    is_acknowledged = Column(Boolean, default=False, comment="是否已确认")
    acknowledged_at = Column(DateTime, nullable=True, comment="确认时间")
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")

    # 索引
    __table_args__ = (
        Index("idx_alert_stock_created", "stock_id", "created_at"),
        Index("idx_alert_is_acknowledged", "is_acknowledged"),
        Index("idx_alert_created_at", "created_at"),
    )

    def __repr__(self):
        return f"<AlertLog({self.stock_id}, type={self.alert_type}, diff={self.price_diff_percent:.2f}%)>"


class Announcement(Base):
    """
    股权激励公告表
    存储从巨潮资讯网抓取的股权激励草案公告信息
    """
    __tablename__ = "announcements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 唯一标识（巨潮的 announcementId）
    announcement_id = Column(String(50), unique=True, nullable=False, comment="公告唯一ID")

    # 股票信息
    stock_code = Column(String(6), nullable=False, index=True, comment="股票代码")
    stock_name = Column(String(100), nullable=False, comment="股票名称")
    exchange = Column(String(2), nullable=False, comment="交易所：SH/SZ")

    # 公告信息
    title = Column(String(500), nullable=False, comment="公告标题")
    publish_date = Column(String(10), index=True, comment="发布日期 YYYY-MM-DD")
    announcement_time = Column(DateTime, nullable=True, comment="公告时间戳（用于判断最新版）")

    # PDF信息
    pdf_url = Column(String(500), nullable=True, comment="PDF链接")
    pdf_path = Column(String(500), nullable=True, comment="本地PDF路径")

    # 提取字段
    plan_type = Column(String(20), default="option", comment="计划类型（目前固定为option）")
    strike_price = Column(Float, nullable=True, comment="行权价（提取自PDF）")
    option_ratio = Column(Float, nullable=True, comment="期权占比%")
    incentive_object_count = Column(Integer, nullable=True, comment="激励对象人数")
    option_allocation = Column(String(2000), nullable=True, comment="期权分配情况")
    performance_requirements = Column(String(3000), nullable=True, comment="业绩考核要求")

    # 筛选结果
    is_eligible = Column(Boolean, default=False, comment="是否符合入库条件")
    filter_reason = Column(String(200), nullable=True, comment="被排除原因（如果不符合）")
    parse_status = Column(String(20), default="pending", comment="解析状态：pending/success/partial/failed")

    # 来源追踪
    source_hash = Column(String(64), nullable=True, comment="去重hash")
    is_latest_of_day = Column(Boolean, default=False, comment="是否为当日最新版（由 Python 层原子控制，不再依赖 DB 触发器）")

    # 生成列：latest 行专用标识，用于唯一索引兜底
    # 仅当 is_latest_of_day=True 时有值，且同一 (stock_code, publish_date) 只能有一条 latest
    latest_key = Column(
        String(30),
        nullable=True,
        comment="latest 行唯一标识：stock_code|publish_date（latest=False 时为 NULL）",
    )

    # 元数据
    created_at = Column(DateTime, default=datetime.utcnow, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment="更新时间")

    # 索引
    __table_args__ = (
        Index("idx_ann_stock_date", "stock_code", "publish_date"),
        Index("idx_ann_publish_date", "publish_date"),
        Index("idx_ann_is_eligible", "is_eligible"),
        Index("idx_ann_parse_status", "parse_status"),
        Index("idx_ann_latest_key", "latest_key", unique=True),
    )

    def __repr__(self):
        return f"<Announcement({self.stock_code} {self.stock_name} {self.publish_date})>"

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "announcement_id": self.announcement_id,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "exchange": self.exchange,
            "title": self.title,
            "publish_date": self.publish_date,
            "announcement_time": self.announcement_time.isoformat() if self.announcement_time else None,
            "pdf_url": self.pdf_url,
            "pdf_path": self.pdf_path,
            "plan_type": self.plan_type,
            "strike_price": self.strike_price,
            "option_ratio": self.option_ratio,
            "incentive_object_count": self.incentive_object_count,
            "option_allocation": self.option_allocation,
            "performance_requirements": self.performance_requirements,
            "is_eligible": self.is_eligible,
            "filter_reason": self.filter_reason,
            "parse_status": self.parse_status,
            "is_latest_of_day": self.is_latest_of_day,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @staticmethod
    def latest_sort_key(announcement_time, announcement_id):
        """
        最新版排序关键字：(announcement_time DESC, announcement_id DESC)
        时间相同则 announcement_id 字典序大的优先
        """
        return (announcement_time, announcement_id)


class EnsureLatestOfDayTrigger:
    """
    DEPRECATED：此触发器已废除，最新公告判定逻辑已全量收归 Python 层。
    触发器在 init_db() 中不再被创建。
    保留此类仅作参考，不可在代码中调用 get_ddl()。
    """
    __table_name__ = "announcements"

    @classmethod
    def get_ddl(cls) -> list:
        """已废除，仅作参考"""
        return []


class WatchTargetChangeLog(Base):
    """
    监控目标变更日志
    记录每次执行价更新的旧值→新值，便于审计追溯
    """
    __tablename__ = "watch_target_change_log"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 关联监控目标
    stock_id = Column(Integer, ForeignKey("stock_watch.id"), nullable=False, index=True, comment="关联监控目标ID")

    # 变更前后
    old_strike_price = Column(Float, nullable=True, comment="变更前执行价")
    new_strike_price = Column(Float, nullable=True, comment="变更后执行价")

    # 来源
    source_type = Column(String(20), nullable=False, comment="变更来源：manual/announcement_auto")
    source_announcement_id = Column(String(50), nullable=True, comment="来源公告ID（如果是自动更新）")

    # 元数据
    changed_at = Column(DateTime, default=datetime.utcnow, comment="变更时间")

    __table_args__ = (
        Index("idx_change_stock_id", "stock_id"),
        Index("idx_change_ann_id", "source_announcement_id"),
        Index("idx_change_changed_at", "changed_at"),
    )

    def __repr__(self):
        return f"<WatchTargetChangeLog({self.stock_id}: {self.old_strike_price}→{self.new_strike_price})>"


class CrawlLog(Base):
    """
    爬取任务运行日志
    记录每次爬取任务的执行情况
    """
    __tablename__ = "crawl_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 任务标识
    job_id = Column(String(100), nullable=False, index=True, comment="任务ID")
    job_type = Column(String(20), nullable=False, comment="任务类型：incremental/backfill")

    # 日期范围
    start_date = Column(String(10), nullable=True, comment="爬取起始日期")
    end_date = Column(String(10), nullable=True, comment="爬取结束日期")

    # 执行状态
    status = Column(String(20), nullable=False, comment="状态：pending/running/success/failed")

    # 统计
    total_fetched = Column(Integer, default=0, comment="抓取总数")
    new_added = Column(Integer, default=0, comment="新增公告数")
    parse_success = Column(Integer, default=0, comment="解析成功数")
    parse_failed = Column(Integer, default=0, comment="解析失败数")
    pdf_download_success = Column(Integer, default=0, comment="PDF下载成功数")
    pdf_download_skipped = Column(Integer, default=0, comment="PDF下载跳过数")
    pdf_download_failed = Column(Integer, default=0, comment="PDF下载失败数")
    watch_targets_created = Column(Integer, default=0, comment="监控目标更新数")

    # 日志
    message = Column(String(1000), nullable=True, comment="日志消息")

    # 时间戳
    started_at = Column(DateTime, nullable=True, comment="开始时间")
    completed_at = Column(DateTime, nullable=True, comment="完成时间")

    __table_args__ = (
        Index("idx_crawl_job_id", "job_id"),
        Index("idx_crawl_status", "status"),
        Index("idx_crawl_started_at", "started_at"),
    )

    def __repr__(self):
        return f"<CrawlLog({self.job_id} {self.status} {self.start_date}~{self.end_date})>"


class EmailLog(Base):
    """
    邮件发送日志
    记录邮件通知的发送结果，便于审计和排障
    """
    __tablename__ = "email_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 邮件类型与关联业务
    notification_type = Column(String(30), nullable=False, comment="邮件类型：crawl_report/alert/generic")
    status = Column(String(20), nullable=False, comment="发送状态：success/failed/skipped")
    job_id = Column(String(100), nullable=True, comment="关联任务ID（如 nightly_crawl）")
    crawl_date = Column(String(10), nullable=True, comment="关联爬取日期 YYYY-MM-DD")
    stock_symbol = Column(String(20), nullable=True, comment="关联股票代码（预警邮件）")

    # 邮件内容摘要
    subject = Column(String(255), nullable=False, comment="邮件主题")
    recipients = Column(String(1000), nullable=True, comment="收件人列表，逗号分隔")
    error_message = Column(String(1000), nullable=True, comment="失败或跳过原因")

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, comment="记录时间")

    __table_args__ = (
        Index("idx_email_status_created", "status", "created_at"),
        Index("idx_email_type_created", "notification_type", "created_at"),
        Index("idx_email_job_id", "job_id"),
    )

    def __repr__(self):
        return f"<EmailLog({self.notification_type} {self.status} {self.subject})>"
