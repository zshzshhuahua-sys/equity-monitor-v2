"""
公告入库服务
负责：公告 → 入库 → 自动更新监控目标 的完整事务流程

规则：
  每只股票只允许 1 个 active 监控目标
  新草案到来 → 更新原目标执行价，保留原记录
  同时写入 WatchTargetChangeLog 变更日志
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Optional
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import AsyncSessionLocal
from ..database.models import Announcement, StockWatch, WatchTargetChangeLog
from .announcement_rule_engine import AnnouncementRuleEngine, AnnouncementRaw, FilterResult

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """返回用于持久化的 naive UTC 时间。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _utc_from_timestamp_ms(timestamp_ms: Optional[int]) -> Optional[datetime]:
    """毫秒时间戳转 naive UTC 时间。"""
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC).replace(tzinfo=None)


@dataclass
class IngestResult:
    """入库结果"""
    announcement_id: str
    stock_code: str
    action: str           # "inserted" / "updated" / "skipped_duplicate" / "failed"
    watch_target_updated: bool
    filter_result: FilterResult
    error: Optional[str] = None


class AnnouncementIngestService:
    """公告入库服务"""

    def __init__(self):
        self.rule_engine = AnnouncementRuleEngine()

    @staticmethod
    def _latest_key(stock_code: str, publish_date: str) -> str:
        return f"{stock_code}|{publish_date}"

    @staticmethod
    def _is_latest_key_conflict(error: IntegrityError) -> bool:
        detail = " ".join(
            part
            for part in (str(getattr(error, "orig", "") or ""), str(error))
            if part
        ).lower()
        is_unique_violation = (
            "unique constraint failed" in detail
            or "duplicate key value violates unique constraint" in detail
            or getattr(getattr(error, "orig", None), "pgcode", None) == "23505"
        )
        if not is_unique_violation:
            return False

        latest_key_signatures = (
            "idx_ann_latest_key",
            "announcements.latest_key",
            "announcement.latest_key",
        )
        if any(signature in detail for signature in latest_key_signatures):
            return True

        # PostgreSQL may surface the logical latest constraint as a composite
        # duplicate on (target_key, latest) instead of the generated latest_key.
        return "target_key" in detail and "latest" in detail

    @staticmethod
    def _supports_select_for_update(session: AsyncSession) -> bool:
        try:
            bind = session.get_bind()
        except Exception:
            return False
        return getattr(bind.dialect, "name", "") != "sqlite"

    async def _load_same_day_announcements(
        self,
        session: AsyncSession,
        stock_code: str,
        publish_date: str,
        *,
        lock_rows: bool,
    ) -> list[Announcement]:
        stmt = select(Announcement).where(
            Announcement.stock_code == stock_code,
            Announcement.publish_date == publish_date,
        )
        if lock_rows and self._supports_select_for_update(session):
            stmt = stmt.with_for_update()

        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _load_announcement_by_id(
        self,
        session: AsyncSession,
        announcement_id: str,
        *,
        lock_row: bool,
    ) -> Optional[Announcement]:
        stmt = select(Announcement).where(
            Announcement.announcement_id == announcement_id
        )
        if lock_row and self._supports_select_for_update(session):
            stmt = stmt.with_for_update()

        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    def _apply_reparse_fields(
        announcement: Announcement,
        *,
        filter_result: FilterResult,
        strike_price: Optional[float],
        option_ratio: Optional[float],
        incentive_object_count: Optional[int],
        option_allocation: str,
        performance_requirements: str,
        parse_status: str,
        pdf_path: Optional[str],
    ) -> None:
        announcement.parse_status = parse_status
        announcement.strike_price = strike_price
        announcement.option_ratio = option_ratio
        announcement.incentive_object_count = incentive_object_count
        announcement.option_allocation = option_allocation
        announcement.performance_requirements = performance_requirements
        announcement.pdf_path = pdf_path or announcement.pdf_path
        announcement.updated_at = _utc_now()
        announcement.is_eligible = filter_result.is_eligible
        announcement.filter_reason = filter_result.exclude_reason

    def _build_announcement(
        self,
        *,
        raw: AnnouncementRaw,
        filter_result: FilterResult,
        strike_price: Optional[float],
        option_ratio: Optional[float],
        incentive_object_count: Optional[int],
        option_allocation: str,
        performance_requirements: str,
        parse_status: str,
        pdf_path: Optional[str],
        exchange: str,
        ann_time: Optional[datetime],
    ) -> Announcement:
        return Announcement(
            announcement_id=raw.announcement_id,
            stock_code=raw.stock_code,
            stock_name=raw.stock_name,
            exchange=exchange,
            title=raw.title,
            publish_date=raw.publish_date,
            announcement_time=ann_time,
            pdf_url=raw.pdf_url or raw.adjunct_url,
            pdf_path=pdf_path,
            plan_type="option",
            strike_price=strike_price,
            option_ratio=option_ratio,
            incentive_object_count=incentive_object_count,
            option_allocation=option_allocation,
            performance_requirements=performance_requirements,
            is_eligible=filter_result.is_eligible,
            filter_reason=filter_result.exclude_reason,
            parse_status=parse_status,
            source_hash=filter_result.source_hash,
            is_latest_of_day=False,
        )

    async def _persist_latest_of_day(
        self,
        session: AsyncSession,
        *,
        current_ann: Announcement,
        latest_key: str,
        is_latest: bool,
        demoted_announcements: list[Announcement],
        promoted_ann: Optional[Announcement],
    ) -> None:
        for old in demoted_announcements:
            old.is_latest_of_day = False
            old.latest_key = None

        await session.flush()

        current_ann.is_latest_of_day = is_latest
        current_ann.latest_key = latest_key if is_latest else None

        if promoted_ann is not None and not is_latest:
            promoted_ann.is_latest_of_day = True
            promoted_ann.latest_key = latest_key

    async def _retry_insert_after_latest_conflict(
        self,
        session: AsyncSession,
        *,
        raw: AnnouncementRaw,
        filter_result: FilterResult,
        strike_price: Optional[float],
        option_ratio: Optional[float],
        incentive_object_count: Optional[int],
        option_allocation: str,
        performance_requirements: str,
        parse_status: str,
        pdf_path: Optional[str],
    ) -> IngestResult:
        existing_ann = await self._load_announcement_by_id(
            session=session,
            announcement_id=raw.announcement_id,
            lock_row=True,
        )
        if existing_ann is not None:
            await session.commit()
            return IngestResult(
                announcement_id=raw.announcement_id,
                stock_code=raw.stock_code,
                action="skipped_duplicate",
                watch_target_updated=False,
                filter_result=filter_result,
            )

        ann_time = _utc_from_timestamp_ms(raw.announcement_time)
        exchange = raw.exchange or self._detect_exchange(raw.stock_code)
        same_day_announcements = await self._load_same_day_announcements(
            session=session,
            stock_code=raw.stock_code,
            publish_date=raw.publish_date,
            lock_rows=True,
        )
        is_latest, demoted_announcements, promoted_ann = self._resolve_latest_of_day(
            same_day_announcements=same_day_announcements,
            ann_time=ann_time,
            announcement_id=raw.announcement_id,
            is_eligible=filter_result.is_eligible,
        )

        ann = self._build_announcement(
            raw=raw,
            filter_result=filter_result,
            strike_price=strike_price,
            option_ratio=option_ratio,
            incentive_object_count=incentive_object_count,
            option_allocation=option_allocation,
            performance_requirements=performance_requirements,
            parse_status=parse_status,
            pdf_path=pdf_path,
            exchange=exchange,
            ann_time=ann_time,
        )
        session.add(ann)
        await session.flush()

        latest_key = self._latest_key(raw.stock_code, raw.publish_date)
        await self._persist_latest_of_day(
            session=session,
            current_ann=ann,
            latest_key=latest_key,
            is_latest=is_latest,
            demoted_announcements=demoted_announcements,
            promoted_ann=promoted_ann,
        )

        watch_updated = False
        if (
            ann.is_latest_of_day
            and filter_result.is_eligible
            and strike_price is not None
            and strike_price > 0
        ):
            watch_updated = await self._upsert_watch_target(
                session=session,
                stock_code=raw.stock_code,
                exchange=exchange,
                strike_price=strike_price,
                source_announcement_id=raw.announcement_id,
                stock_name=raw.stock_name,
            )

        await session.commit()
        return IngestResult(
            announcement_id=raw.announcement_id,
            stock_code=raw.stock_code,
            action="inserted",
            watch_target_updated=watch_updated,
            filter_result=filter_result,
        )

    async def _retry_force_reparse_after_latest_conflict(
        self,
        session: AsyncSession,
        *,
        raw: AnnouncementRaw,
        filter_result: FilterResult,
        strike_price: Optional[float],
        option_ratio: Optional[float],
        incentive_object_count: Optional[int],
        option_allocation: str,
        performance_requirements: str,
        parse_status: str,
        pdf_path: Optional[str],
    ) -> IngestResult:
        existing_ann = await self._load_announcement_by_id(
            session=session,
            announcement_id=raw.announcement_id,
            lock_row=True,
        )
        if existing_ann is None:
            raise RuntimeError(f"force_reparse retry missing announcement: {raw.announcement_id}")

        self._apply_reparse_fields(
            existing_ann,
            filter_result=filter_result,
            strike_price=strike_price,
            option_ratio=option_ratio,
            incentive_object_count=incentive_object_count,
            option_allocation=option_allocation,
            performance_requirements=performance_requirements,
            parse_status=parse_status,
            pdf_path=pdf_path,
        )

        is_latest, demoted_announcements, promoted_ann = await self._is_latest_of_day(
            session=session,
            stock_code=existing_ann.stock_code,
            publish_date=existing_ann.publish_date,
            ann_time=existing_ann.announcement_time,
            announcement_id=raw.announcement_id,
            is_eligible=filter_result.is_eligible,
        )
        latest_key = self._latest_key(existing_ann.stock_code, existing_ann.publish_date)
        await self._persist_latest_of_day(
            session=session,
            current_ann=existing_ann,
            latest_key=latest_key,
            is_latest=is_latest,
            demoted_announcements=demoted_announcements,
            promoted_ann=promoted_ann,
        )

        watch_updated = False
        if (
            existing_ann.is_latest_of_day
            and filter_result.is_eligible
            and strike_price is not None
            and strike_price > 0
        ):
            watch_updated = await self._upsert_watch_target(
                session=session,
                stock_code=raw.stock_code,
                exchange=existing_ann.exchange or self._detect_exchange(raw.stock_code),
                strike_price=strike_price,
                source_announcement_id=raw.announcement_id,
                stock_name=raw.stock_name,
            )

        await session.commit()
        return IngestResult(
            announcement_id=raw.announcement_id,
            stock_code=raw.stock_code,
            action="updated",
            watch_target_updated=watch_updated,
            filter_result=filter_result,
        )

    async def ingest_announcement(
        self,
        raw: AnnouncementRaw,
        strike_price: Optional[float] = None,
        option_ratio: Optional[float] = None,
        incentive_object_count: Optional[int] = None,
        option_allocation: str = "",
        performance_requirements: str = "",
        parse_status: str = "pending",
        pdf_path: Optional[str] = None,
        force_reparse: bool = False,
    ) -> IngestResult:
        """
        公告入库主流程（原子事务内完成，无 DB 触发器依赖）

        规则（核心）：
          - 同一股同日只允许1条符合条件的最新公告触发 watch 更新
          - 旧公告入库但不更新监控目标
          - 同一股同日最新版判定：仅在符合条件公告中比较 announcement_time，announcement_id 最大决胜
        """
        filter_result = self.rule_engine.filter(raw)
        existing_ann: Optional[Announcement] = None

        async with AsyncSessionLocal() as session:
            try:
                # 查重
                existing = await session.execute(
                    select(Announcement).where(
                        Announcement.announcement_id == raw.announcement_id
                    )
                )
                existing_ann = existing.scalar_one_or_none()
                if existing_ann:
                    if force_reparse:
                        watch_updated = await self._force_reparse(
                            session=session,
                            existing_ann=existing_ann,
                            raw=raw,
                            filter_result=filter_result,
                            strike_price=strike_price,
                            option_ratio=option_ratio,
                            incentive_object_count=incentive_object_count,
                            option_allocation=option_allocation,
                            performance_requirements=performance_requirements,
                            parse_status=parse_status,
                            pdf_path=pdf_path,
                        )
                        await session.commit()
                        return IngestResult(
                            announcement_id=raw.announcement_id,
                            stock_code=raw.stock_code,
                            action="updated",
                            watch_target_updated=watch_updated,
                            filter_result=filter_result,
                        )
                    return IngestResult(
                        announcement_id=raw.announcement_id,
                        stock_code=raw.stock_code,
                        action="skipped_duplicate",
                        watch_target_updated=False,
                        filter_result=filter_result,
                    )

                ann_time = _utc_from_timestamp_ms(raw.announcement_time)
                exchange = raw.exchange or self._detect_exchange(raw.stock_code)

                # 判断是否为最新（全局竞争）
                is_latest, demoted_announcements, promoted_ann = await self._is_latest_of_day(
                    session=session,
                    stock_code=raw.stock_code,
                    publish_date=raw.publish_date,
                    ann_time=ann_time,
                    announcement_id=raw.announcement_id,
                    is_eligible=filter_result.is_eligible,
                )

                ann = self._build_announcement(
                    raw=raw,
                    filter_result=filter_result,
                    strike_price=strike_price,
                    option_ratio=option_ratio,
                    incentive_object_count=incentive_object_count,
                    option_allocation=option_allocation,
                    performance_requirements=performance_requirements,
                    parse_status=parse_status,
                    pdf_path=pdf_path,
                    exchange=exchange,
                    ann_time=ann_time,
                )
                session.add(ann)
                await session.flush()

                watch_updated = False
                latest_key = self._latest_key(raw.stock_code, raw.publish_date)
                await self._persist_latest_of_day(
                    session=session,
                    current_ann=ann,
                    latest_key=latest_key,
                    is_latest=is_latest,
                    demoted_announcements=demoted_announcements,
                    promoted_ann=promoted_ann,
                )

                if (
                    ann.is_latest_of_day
                    and filter_result.is_eligible
                    and strike_price is not None
                    and strike_price > 0
                ):
                    watch_updated = await self._upsert_watch_target(
                        session=session,
                        stock_code=raw.stock_code,
                        exchange=exchange,
                        strike_price=strike_price,
                        source_announcement_id=raw.announcement_id,
                        stock_name=raw.stock_name,
                    )

                await session.commit()

                logger.info(
                    "公告入库: %s %s is_latest=%s watch_updated=%s",
                    raw.stock_code, raw.announcement_id,
                    is_latest, watch_updated,
                )

                return IngestResult(
                    announcement_id=raw.announcement_id,
                    stock_code=raw.stock_code,
                    action="inserted",
                    watch_target_updated=watch_updated,
                    filter_result=filter_result,
                )

            except IntegrityError as exc:
                await session.rollback()
                if not self._is_latest_key_conflict(exc):
                    logger.exception("公告入库约束失败: %s", raw.announcement_id)
                    return IngestResult(
                        announcement_id=raw.announcement_id,
                        stock_code=raw.stock_code,
                        action="failed",
                        watch_target_updated=False,
                        filter_result=filter_result,
                        error=str(exc),
                    )

                logger.info("latest_key 并发冲突，基于 committed state 重算: %s", raw.announcement_id)
                try:
                    conflict_ann = await self._load_announcement_by_id(
                        session=session,
                        announcement_id=raw.announcement_id,
                        lock_row=True,
                    )

                    if force_reparse and conflict_ann is not None:
                        return await self._retry_force_reparse_after_latest_conflict(
                            session=session,
                            raw=raw,
                            filter_result=filter_result,
                            strike_price=strike_price,
                            option_ratio=option_ratio,
                            incentive_object_count=incentive_object_count,
                            option_allocation=option_allocation,
                            performance_requirements=performance_requirements,
                            parse_status=parse_status,
                            pdf_path=pdf_path,
                        )

                    return await self._retry_insert_after_latest_conflict(
                        session=session,
                        raw=raw,
                        filter_result=filter_result,
                        strike_price=strike_price,
                        option_ratio=option_ratio,
                        incentive_object_count=incentive_object_count,
                        option_allocation=option_allocation,
                        performance_requirements=performance_requirements,
                        parse_status=parse_status,
                        pdf_path=pdf_path,
                    )
                except Exception as retry_err:
                    await session.rollback()
                    logger.exception("latest_key 冲突重算失败: %s", raw.announcement_id)
                    return IngestResult(
                        announcement_id=raw.announcement_id,
                        stock_code=raw.stock_code,
                        action="failed",
                        watch_target_updated=False,
                        filter_result=filter_result,
                        error=str(retry_err),
                    )

            except Exception as e:
                await session.rollback()
                logger.exception("公告入库失败: %s", raw.announcement_id)
                return IngestResult(
                    announcement_id=raw.announcement_id,
                    stock_code=raw.stock_code,
                    action="failed",
                    watch_target_updated=False,
                    filter_result=filter_result,
                    error=str(e),
                )

    async def _force_reparse(
        self,
        session: AsyncSession,
        existing_ann,
        raw,
        filter_result,
        strike_price,
        option_ratio,
        incentive_object_count,
        option_allocation,
        performance_requirements,
        parse_status,
        pdf_path,
    ) -> bool:
        """
        force_reparse：原地更新字段，重算 latest 状态。
        watch_target 更新与公告状态变更在同一事务内完成。
        Returns: watch_target 是否被更新
        """
        self._apply_reparse_fields(
            existing_ann,
            filter_result=filter_result,
            strike_price=strike_price,
            option_ratio=option_ratio,
            incentive_object_count=incentive_object_count,
            option_allocation=option_allocation,
            performance_requirements=performance_requirements,
            parse_status=parse_status,
            pdf_path=pdf_path,
        )

        watch_updated = False

        is_latest, demoted_announcements, promoted_ann = await self._is_latest_of_day(
            session=session,
            stock_code=existing_ann.stock_code,
            publish_date=existing_ann.publish_date,
            ann_time=existing_ann.announcement_time,
            announcement_id=raw.announcement_id,
            is_eligible=filter_result.is_eligible,
        )
        latest_key = self._latest_key(existing_ann.stock_code, existing_ann.publish_date)
        await self._persist_latest_of_day(
            session=session,
            current_ann=existing_ann,
            latest_key=latest_key,
            is_latest=is_latest,
            demoted_announcements=demoted_announcements,
            promoted_ann=promoted_ann,
        )

        # 只有当前公告仍是 latest 才更新监控目标（与主插入路径语义一致）
        if (
            existing_ann.is_latest_of_day
            and filter_result.is_eligible
            and strike_price is not None
            and strike_price > 0
        ):
            watch_updated = await self._upsert_watch_target(
                session=session,
                stock_code=raw.stock_code,
                exchange=existing_ann.exchange or self._detect_exchange(raw.stock_code),
                strike_price=strike_price,
                source_announcement_id=raw.announcement_id,
                stock_name=raw.stock_name,
            )

        return watch_updated

    async def _is_latest_of_day(
        self,
        session: AsyncSession,
        stock_code: str,
        publish_date: str,
        ann_time: Optional[datetime],
        announcement_id: str,
        is_eligible: bool,
    ) -> tuple[bool, list[Announcement], Optional[Announcement]]:
        """
        判断是否为同日最新公告。

        仅在符合条件的公告中比较先后顺序，同时修复历史脏数据：
          - ineligible 公告永远不能成为 latest
          - 若历史上已有 ineligible latest，会在本次写入时被降级
          - 若正确的 eligible winner 尚未被标记，也会在 caller 中补齐

        Returns:
            (当前公告是否为最新, 需要降级的已标记 latest 公告列表, 需要补标为 latest 的旧公告)
        """
        same_day_announcements = await self._load_same_day_announcements(
            session=session,
            stock_code=stock_code,
            publish_date=publish_date,
            lock_rows=True,
        )
        return self._resolve_latest_of_day(
            same_day_announcements=same_day_announcements,
            ann_time=ann_time,
            announcement_id=announcement_id,
            is_eligible=is_eligible,
        )

    @staticmethod
    def _resolve_latest_of_day(
        *,
        same_day_announcements: list[Announcement],
        ann_time: Optional[datetime],
        announcement_id: str,
        is_eligible: bool,
    ) -> tuple[bool, list[Announcement], Optional[Announcement]]:
        existing_latest = [
            ann for ann in same_day_announcements if ann.is_latest_of_day
        ]

        from datetime import datetime as dt
        _MIN_DT = dt.min

        def safe_key(t: Optional[dt], aid: str):
            return (t or _MIN_DT, aid)

        current_key = safe_key(ann_time, announcement_id)
        eligible_existing = [
            ann
            for ann in same_day_announcements
            if ann.is_eligible and ann.announcement_id != announcement_id
        ]

        winning_existing = None
        if eligible_existing:
            winning_existing = max(
                eligible_existing,
                key=lambda ann: safe_key(ann.announcement_time, ann.announcement_id),
            )

        if not is_eligible:
            return False, [
                ann
                for ann in existing_latest
                if winning_existing is None
                or ann.announcement_id != winning_existing.announcement_id
            ], winning_existing

        if winning_existing is None:
            return True, existing_latest, None

        winning_key = safe_key(
            winning_existing.announcement_time,
            winning_existing.announcement_id,
        )
        if current_key >= winning_key:
            return True, existing_latest, None

        return False, [
            ann
            for ann in existing_latest
            if ann.announcement_id != winning_existing.announcement_id
        ], winning_existing

    async def _upsert_watch_target(
        self,
        session: AsyncSession,
        stock_code: str,
        exchange: str,
        strike_price: float,
        source_announcement_id: str,
        stock_name: str = "",
    ) -> bool:
        """
        更新/创建监控目标

        策略：每只股票只允许 1 个 active 监控目标
        新草案到来 → 更新原目标执行价，保留原记录，写变更日志
        """
        full_code = f"{stock_code}.{exchange}"

        # 查现有监控目标
        result = await session.execute(
            select(StockWatch).where(
                StockWatch.symbol == stock_code,
                StockWatch.exchange == exchange,
            )
        )
        existing_watch = result.scalar_one_or_none()

        if existing_watch:
            old_price = existing_watch.strike_price

            # 记录变更日志
            if old_price != strike_price:
                change_log = WatchTargetChangeLog(
                    stock_id=existing_watch.id,
                    old_strike_price=old_price,
                    new_strike_price=strike_price,
                    source_type="announcement_auto",
                    source_announcement_id=source_announcement_id,
                )
                session.add(change_log)

            # 更新执行价
            existing_watch.strike_price = strike_price
            existing_watch.updated_at = _utc_now()
            # 名称为空时补充（已有名字不覆盖）
            if not existing_watch.name and stock_name:
                existing_watch.name = stock_name
            # 自动启用
            existing_watch.is_active = True
            logger.info(
                "更新监控目标执行价: %s %s → %.4f (旧=%.4f)",
                full_code, source_announcement_id, strike_price, old_price or 0,
            )

        else:
            # 新建时直接写入股票名称
            new_watch = StockWatch(
                symbol=stock_code,
                exchange=exchange,
                full_code=full_code,
                name=stock_name or "",
                strike_price=strike_price,
                is_active=True,
                updated_at=_utc_now(),
            )
            session.add(new_watch)
            await session.flush()

            # 记录新建日志
            change_log = WatchTargetChangeLog(
                stock_id=new_watch.id,
                old_strike_price=None,
                new_strike_price=strike_price,
                source_type="announcement_auto",
                source_announcement_id=source_announcement_id,
            )
            session.add(change_log)
            logger.info("新建监控目标: %s %s strike=%.4f", full_code, stock_name or "", strike_price)

        return True

    @staticmethod
    def _detect_exchange(stock_code: str) -> str:
        """根据股票代码推断交易所"""
        if stock_code.startswith("6"):
            return "SH"
        return "SZ"


# 全局实例
announcement_ingest_service = AnnouncementIngestService()
