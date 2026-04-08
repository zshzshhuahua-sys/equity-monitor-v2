"""
公告去重与最新版判定服务

规则：
  同一只股票、同一天有多条草案公告时：
    - 按 announcement_time desc 排序
    - 只保留第一条（最新版）
    - 其余标记 is_latest_of_day = False，或直接丢弃
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime

from ..database.models import Announcement


@dataclass
class AnnouncementRecord:
    """公告记录（含内存排序信息）"""
    raw_id: str                      # announcement_id
    stock_code: str
    publish_date: str
    announcement_time: Optional[datetime]
    # 其他字段透传
    title: str = ""
    stock_name: str = ""
    pdf_url: str = ""
    exchange: str = "SZ"


class AnnouncementDedupService:
    """公告去重与最新版判定服务"""

    @staticmethod
    def sort_key(record: AnnouncementRecord) -> tuple:
        """
        排序关键字：announcement_time 大的排前面
        announcement_time 为 None 时降级用 stock_code + raw_id 排序
        """
        ts = record.announcement_time.timestamp() if record.announcement_time else 0
        return (ts, record.stock_code, record.raw_id)

    @classmethod
    def pick_latest_of_day(
        cls, records: List[AnnouncementRecord]
    ) -> List[AnnouncementRecord]:
        """
        同股同日多条公告，只保留每组第一条（最新）

        Args:
            records: 原始公告列表（应已按 announcement_id 去重）

        Returns:
            保留的公告列表（其余被丢弃）
        """
        if not records:
            return []

        # 1. 按 announcement_time 降序排序
        sorted_records = sorted(records, key=cls.sort_key, reverse=True)

        # 2. 分组：同 stock_code + publish_date 只留第一条
        seen: set = set()
        keep: List[AnnouncementRecord] = []

        for rec in sorted_records:
            key = (rec.stock_code, rec.publish_date)
            if key not in seen:
                seen.add(key)
                keep.append(rec)
                # 标记其余为非最新版（如果记录本身支持此字段）
                # 注意：这里返回的是"应该保留的"，其余 caller 自行丢弃

        return keep

    @staticmethod
    def mark_latest_of_day_inplace(records: List[Announcement]) -> None:
        """
        给一组 Announcement 对象原地标记 is_latest_of_day

        适用于从数据库查出一批同股同天后，按 announcement_time 排序打标。
        """
        if not records:
            return

        # 按 announcement_time 降序
        sorted_records = sorted(
            records,
            key=lambda r: r.announcement_time.timestamp() if r.announcement_time else 0,
            reverse=True,
        )

        seen: set = set()
        for rec in sorted_records:
            key = (rec.stock_code, rec.publish_date)
            if key not in seen:
                seen.add(key)
                rec.is_latest_of_day = True
            else:
                rec.is_latest_of_day = False
