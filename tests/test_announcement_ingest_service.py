import os
import sqlite3
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.database.models import Announcement, Base, StockWatch
from src.services.announcement_ingest_service import AnnouncementIngestService
from src.services.announcement_rule_engine import AnnouncementRaw


def _announcement_time_ms(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


class AnnouncementIngestServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db_path = os.path.abspath("tests/announcement_ingest_service.db")
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_path}")
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            autoflush=False,
        )

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.session_patch = patch(
            "src.services.announcement_ingest_service.AsyncSessionLocal",
            self.session_factory,
        )
        self.session_patch.start()
        self.service = AnnouncementIngestService()

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    @staticmethod
    def _build_raw(
        announcement_id: str,
        title: str,
        announcement_time: int,
    ) -> AnnouncementRaw:
        return AnnouncementRaw(
            announcement_id=announcement_id,
            stock_code="002947",
            stock_name="恒铭达",
            exchange="SZ",
            title=title,
            publish_date="2026-04-08",
            announcement_time=announcement_time,
            pdf_url=f"https://example.com/{announcement_id}.pdf",
        )

    async def _fetch_announcements(self) -> dict[str, Announcement]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(Announcement).order_by(Announcement.announcement_id.asc())
            )
            return {
                announcement.announcement_id: announcement
                for announcement in result.scalars().all()
            }

    async def _fetch_watch(self) -> StockWatch | None:
        async with self.session_factory() as session:
            result = await session.execute(select(StockWatch))
            return result.scalar_one_or_none()

    def _service_session_factory_with_flag(self, flag: str):
        def factory():
            session = self.session_factory()
            session.info[flag] = True
            return session

        return factory

    @staticmethod
    def _stored_datetime(timestamp_ms: int) -> datetime:
        return datetime.fromtimestamp(timestamp_ms / 1000, UTC).replace(tzinfo=None)

    async def test_ineligible_report_does_not_block_same_day_draft(self):
        report_raw = self._build_raw(
            "ann-report",
            "2026年股票期权激励计划独立财务顾问报告",
            _announcement_time_ms(2026, 4, 8, 10, 30),
        )
        draft_raw = self._build_raw(
            "ann-draft",
            "2026年股票期权激励计划（草案）",
            _announcement_time_ms(2026, 4, 8, 9, 0),
        )

        report_result = await self.service.ingest_announcement(
            raw=report_raw,
            strike_price=21.5,
            parse_status="success",
        )
        draft_result = await self.service.ingest_announcement(
            raw=draft_raw,
            strike_price=21.5,
            parse_status="success",
        )

        announcements = await self._fetch_announcements()
        watch = await self._fetch_watch()

        self.assertFalse(report_result.filter_result.is_eligible)
        self.assertFalse(report_result.watch_target_updated)
        self.assertTrue(draft_result.filter_result.is_eligible)
        self.assertTrue(draft_result.watch_target_updated)

        report_ann = announcements["ann-report"]
        draft_ann = announcements["ann-draft"]

        self.assertFalse(report_ann.is_latest_of_day)
        self.assertIsNone(report_ann.latest_key)
        self.assertTrue(draft_ann.is_latest_of_day)
        self.assertEqual(draft_ann.latest_key, "002947|2026-04-08")

        self.assertIsNotNone(watch)
        self.assertEqual(watch.symbol, "002947")
        self.assertEqual(watch.exchange, "SZ")
        self.assertEqual(watch.name, "恒铭达")
        self.assertEqual(watch.strike_price, 21.5)

    async def test_force_reparse_recovers_latest_from_legacy_ineligible_report(self):
        report_raw = self._build_raw(
            "legacy-report",
            "2026年股票期权激励计划独立财务顾问报告",
            _announcement_time_ms(2026, 4, 8, 10, 30),
        )
        draft_raw = self._build_raw(
            "legacy-draft",
            "2026年股票期权激励计划（草案）",
            _announcement_time_ms(2026, 4, 8, 9, 0),
        )

        async with self.session_factory() as session:
            session.add_all(
                [
                    Announcement(
                        announcement_id=report_raw.announcement_id,
                        stock_code=report_raw.stock_code,
                        stock_name=report_raw.stock_name,
                        exchange=report_raw.exchange,
                        title=report_raw.title,
                        publish_date=report_raw.publish_date,
                        announcement_time=self._stored_datetime(report_raw.announcement_time),
                        pdf_url=report_raw.pdf_url,
                        is_eligible=False,
                        filter_reason="标题含排除词「独立财务顾问报告」",
                        parse_status="success",
                        is_latest_of_day=True,
                        latest_key="002947|2026-04-08",
                    ),
                    Announcement(
                        announcement_id=draft_raw.announcement_id,
                        stock_code=draft_raw.stock_code,
                        stock_name=draft_raw.stock_name,
                        exchange=draft_raw.exchange,
                        title=draft_raw.title,
                        publish_date=draft_raw.publish_date,
                        announcement_time=self._stored_datetime(draft_raw.announcement_time),
                        pdf_url=draft_raw.pdf_url,
                        is_eligible=True,
                        filter_reason=None,
                        parse_status="pending",
                        is_latest_of_day=False,
                        latest_key=None,
                    ),
                ]
            )
            await session.commit()

        result = await self.service.ingest_announcement(
            raw=draft_raw,
            strike_price=18.8,
            parse_status="success",
            force_reparse=True,
        )

        announcements = await self._fetch_announcements()
        watch = await self._fetch_watch()

        self.assertEqual(result.action, "updated")
        self.assertTrue(result.watch_target_updated)

        report_ann = announcements["legacy-report"]
        draft_ann = announcements["legacy-draft"]

        self.assertFalse(report_ann.is_latest_of_day)
        self.assertIsNone(report_ann.latest_key)
        self.assertTrue(draft_ann.is_latest_of_day)
        self.assertEqual(draft_ann.latest_key, "002947|2026-04-08")
        self.assertEqual(draft_ann.strike_price, 18.8)
        self.assertEqual(draft_ann.parse_status, "success")

        self.assertIsNotNone(watch)
        self.assertEqual(watch.strike_price, 18.8)

    async def test_new_ineligible_insert_repairs_legacy_latest_flag(self):
        report_raw = self._build_raw(
            "legacy-report",
            "2026年股票期权激励计划独立财务顾问报告",
            _announcement_time_ms(2026, 4, 8, 10, 30),
        )
        draft_raw = self._build_raw(
            "legacy-draft",
            "2026年股票期权激励计划（草案）",
            _announcement_time_ms(2026, 4, 8, 9, 0),
        )
        newer_report_raw = self._build_raw(
            "new-report",
            "2026年股票期权激励计划独立财务顾问报告（补充版）",
            _announcement_time_ms(2026, 4, 8, 11, 0),
        )

        async with self.session_factory() as session:
            session.add_all(
                [
                    Announcement(
                        announcement_id=report_raw.announcement_id,
                        stock_code=report_raw.stock_code,
                        stock_name=report_raw.stock_name,
                        exchange=report_raw.exchange,
                        title=report_raw.title,
                        publish_date=report_raw.publish_date,
                        announcement_time=self._stored_datetime(report_raw.announcement_time),
                        pdf_url=report_raw.pdf_url,
                        is_eligible=False,
                        filter_reason="标题含排除词「独立财务顾问报告」",
                        parse_status="success",
                        is_latest_of_day=True,
                        latest_key="002947|2026-04-08",
                    ),
                    Announcement(
                        announcement_id=draft_raw.announcement_id,
                        stock_code=draft_raw.stock_code,
                        stock_name=draft_raw.stock_name,
                        exchange=draft_raw.exchange,
                        title=draft_raw.title,
                        publish_date=draft_raw.publish_date,
                        announcement_time=self._stored_datetime(draft_raw.announcement_time),
                        pdf_url=draft_raw.pdf_url,
                        is_eligible=True,
                        filter_reason=None,
                        parse_status="success",
                        is_latest_of_day=False,
                        latest_key=None,
                    ),
                ]
            )
            await session.commit()

        result = await self.service.ingest_announcement(
            raw=newer_report_raw,
            strike_price=19.2,
            parse_status="success",
        )

        announcements = await self._fetch_announcements()

        self.assertFalse(result.filter_result.is_eligible)
        self.assertFalse(result.watch_target_updated)
        self.assertFalse(announcements["legacy-report"].is_latest_of_day)
        self.assertFalse(announcements["new-report"].is_latest_of_day)
        self.assertTrue(announcements["legacy-draft"].is_latest_of_day)
        self.assertEqual(announcements["legacy-draft"].latest_key, "002947|2026-04-08")

    async def test_latest_key_conflict_recomputes_and_promotes_newer_insert(self):
        newer_raw = self._build_raw(
            "new-draft",
            "2026年股票期权激励计划（草案第二版）",
            _announcement_time_ms(2026, 4, 8, 11, 0),
        )
        older_raw = self._build_raw(
            "old-draft",
            "2026年股票期权激励计划（草案）",
            _announcement_time_ms(2026, 4, 8, 9, 0),
        )
        original_commit = AsyncSession.commit
        original_rollback = AsyncSession.rollback

        async def inject_latest_conflict(session, *args, **kwargs):
            if session.info.get("inject_latest_conflict") and not session.info.get("latest_conflict_triggered"):
                session.info["latest_conflict_triggered"] = True
                session.info["seed_competitor_after_rollback"] = True
                raise IntegrityError(
                    "INSERT INTO announcements ...",
                    None,
                    sqlite3.IntegrityError("UNIQUE constraint failed: announcements.latest_key"),
                )

            return await original_commit(session, *args, **kwargs)

        async def inject_latest_conflict_rollback(session, *args, **kwargs):
            await original_rollback(session, *args, **kwargs)
            if session.info.pop("seed_competitor_after_rollback", False):
                async with self.session_factory() as competing_session:
                    competing_session.add(
                        Announcement(
                            announcement_id=older_raw.announcement_id,
                            stock_code=older_raw.stock_code,
                            stock_name=older_raw.stock_name,
                            exchange=older_raw.exchange,
                            title=older_raw.title,
                            publish_date=older_raw.publish_date,
                            announcement_time=self._stored_datetime(older_raw.announcement_time),
                            pdf_url=older_raw.pdf_url,
                            strike_price=18.6,
                            is_eligible=True,
                            parse_status="success",
                            is_latest_of_day=True,
                            latest_key="002947|2026-04-08",
                        )
                    )
                    await original_commit(competing_session)

        with (
            patch(
                "src.services.announcement_ingest_service.AsyncSessionLocal",
                self._service_session_factory_with_flag("inject_latest_conflict"),
            ),
            patch.object(AsyncSession, "commit", new=inject_latest_conflict),
            patch.object(AsyncSession, "rollback", new=inject_latest_conflict_rollback),
        ):
            result = await self.service.ingest_announcement(
                raw=newer_raw,
                strike_price=21.9,
                parse_status="success",
            )

        announcements = await self._fetch_announcements()
        watch = await self._fetch_watch()

        self.assertEqual(result.action, "inserted")
        self.assertTrue(result.watch_target_updated)
        self.assertTrue(announcements["new-draft"].is_latest_of_day)
        self.assertEqual(announcements["new-draft"].latest_key, "002947|2026-04-08")
        self.assertFalse(announcements["old-draft"].is_latest_of_day)
        self.assertIsNone(announcements["old-draft"].latest_key)
        self.assertIsNotNone(watch)
        self.assertEqual(watch.strike_price, 21.9)

    async def test_non_latest_integrity_error_fails_instead_of_demoting(self):
        raw = self._build_raw(
            "ann-failure",
            "2026年股票期权激励计划（草案）",
            _announcement_time_ms(2026, 4, 8, 10, 0),
        )
        original_commit = AsyncSession.commit

        async def inject_non_latest_conflict(session, *args, **kwargs):
            if session.info.get("inject_non_latest_conflict") and not session.info.get("non_latest_conflict_triggered"):
                session.info["non_latest_conflict_triggered"] = True
                raise IntegrityError(
                    "INSERT INTO announcements ...",
                    None,
                    sqlite3.IntegrityError("UNIQUE constraint failed: announcements.announcement_id"),
                )

            return await original_commit(session, *args, **kwargs)

        with (
            patch(
                "src.services.announcement_ingest_service.AsyncSessionLocal",
                self._service_session_factory_with_flag("inject_non_latest_conflict"),
            ),
            patch.object(AsyncSession, "commit", new=inject_non_latest_conflict),
        ):
            result = await self.service.ingest_announcement(
                raw=raw,
                strike_price=20.5,
                parse_status="success",
            )

        announcements = await self._fetch_announcements()

        self.assertEqual(result.action, "failed")
        self.assertIn("announcement_id", result.error)
        self.assertEqual(announcements, {})

    def test_latest_key_conflict_detector_only_matches_latest_unique_violations(self):
        self.assertTrue(
            self.service._is_latest_key_conflict(
                IntegrityError(
                    "INSERT INTO announcements ...",
                    None,
                    sqlite3.IntegrityError("UNIQUE constraint failed: announcements.latest_key"),
                )
            )
        )
        self.assertTrue(
            self.service._is_latest_key_conflict(
                IntegrityError(
                    "UPDATE announcements ...",
                    None,
                    Exception(
                        'duplicate key value violates unique constraint "uq_ann_target_latest"\n'
                        "DETAIL: Key (target_key, latest)=(002947|2026-04-08, 1) already exists."
                    ),
                )
            )
        )
        self.assertFalse(
            self.service._is_latest_key_conflict(
                IntegrityError(
                    "INSERT INTO announcements ...",
                    None,
                    sqlite3.IntegrityError("UNIQUE constraint failed: announcements.announcement_id"),
                )
            )
        )
        self.assertFalse(
            self.service._is_latest_key_conflict(
                IntegrityError(
                    "UPDATE announcements ...",
                    None,
                    Exception("CHECK constraint failed: latest_key must be null when latest=0"),
                )
            )
        )

    async def test_force_reparse_latest_key_conflict_recomputes_without_unbound_ann(self):
        current_raw = self._build_raw(
            "existing-draft",
            "2026年股票期权激励计划（草案第二版）",
            _announcement_time_ms(2026, 4, 8, 11, 0),
        )
        older_raw = self._build_raw(
            "concurrent-draft",
            "2026年股票期权激励计划（草案）",
            _announcement_time_ms(2026, 4, 8, 9, 0),
        )

        async with self.session_factory() as session:
            session.add(
                Announcement(
                    announcement_id=current_raw.announcement_id,
                    stock_code=current_raw.stock_code,
                    stock_name=current_raw.stock_name,
                    exchange=current_raw.exchange,
                    title=current_raw.title,
                    publish_date=current_raw.publish_date,
                    announcement_time=self._stored_datetime(current_raw.announcement_time),
                    pdf_url=current_raw.pdf_url,
                    is_eligible=True,
                    parse_status="pending",
                    is_latest_of_day=False,
                    latest_key=None,
                )
            )
            await session.commit()

        original_commit = AsyncSession.commit
        original_rollback = AsyncSession.rollback

        async def inject_reparse_conflict(session, *args, **kwargs):
            if session.info.get("inject_reparse_conflict") and not session.info.get("reparse_conflict_triggered"):
                session.info["reparse_conflict_triggered"] = True
                session.info["seed_competitor_after_rollback"] = True
                raise IntegrityError(
                    "UPDATE announcements ...",
                    None,
                    sqlite3.IntegrityError("UNIQUE constraint failed: announcements.latest_key"),
                )

            return await original_commit(session, *args, **kwargs)

        async def inject_reparse_conflict_rollback(session, *args, **kwargs):
            await original_rollback(session, *args, **kwargs)
            if session.info.pop("seed_competitor_after_rollback", False):
                async with self.session_factory() as competing_session:
                    competing_session.add(
                        Announcement(
                            announcement_id=older_raw.announcement_id,
                            stock_code=older_raw.stock_code,
                            stock_name=older_raw.stock_name,
                            exchange=older_raw.exchange,
                            title=older_raw.title,
                            publish_date=older_raw.publish_date,
                            announcement_time=self._stored_datetime(older_raw.announcement_time),
                            pdf_url=older_raw.pdf_url,
                            strike_price=18.1,
                            is_eligible=True,
                            parse_status="success",
                            is_latest_of_day=True,
                            latest_key="002947|2026-04-08",
                        )
                    )
                    await original_commit(competing_session)

        with (
            patch(
                "src.services.announcement_ingest_service.AsyncSessionLocal",
                self._service_session_factory_with_flag("inject_reparse_conflict"),
            ),
            patch.object(AsyncSession, "commit", new=inject_reparse_conflict),
            patch.object(AsyncSession, "rollback", new=inject_reparse_conflict_rollback),
        ):
            result = await self.service.ingest_announcement(
                raw=current_raw,
                strike_price=22.4,
                parse_status="success",
                force_reparse=True,
            )

        announcements = await self._fetch_announcements()
        watch = await self._fetch_watch()

        self.assertEqual(result.action, "updated")
        self.assertTrue(result.watch_target_updated)
        self.assertTrue(announcements["existing-draft"].is_latest_of_day)
        self.assertEqual(announcements["existing-draft"].latest_key, "002947|2026-04-08")
        self.assertEqual(announcements["existing-draft"].strike_price, 22.4)
        self.assertEqual(announcements["existing-draft"].parse_status, "success")
        self.assertFalse(announcements["concurrent-draft"].is_latest_of_day)
        self.assertIsNone(announcements["concurrent-draft"].latest_key)
        self.assertIsNotNone(watch)
        self.assertEqual(watch.strike_price, 22.4)

    async def test_force_reparse_latest_key_conflict_requeries_same_announcement_inserted_by_racer(self):
        raw = self._build_raw(
            "same-draft",
            "2026年股票期权激励计划（草案第二版）",
            _announcement_time_ms(2026, 4, 8, 11, 0),
        )
        original_commit = AsyncSession.commit
        original_rollback = AsyncSession.rollback

        async def inject_reparse_conflict(session, *args, **kwargs):
            if session.info.get("inject_same_ann_conflict") and not session.info.get("same_ann_conflict_triggered"):
                session.info["same_ann_conflict_triggered"] = True
                session.info["seed_same_announcement_after_rollback"] = True
                raise IntegrityError(
                    "INSERT INTO announcements ...",
                    None,
                    sqlite3.IntegrityError("UNIQUE constraint failed: announcements.latest_key"),
                )

            return await original_commit(session, *args, **kwargs)

        async def inject_reparse_conflict_rollback(session, *args, **kwargs):
            await original_rollback(session, *args, **kwargs)
            if session.info.pop("seed_same_announcement_after_rollback", False):
                async with self.session_factory() as competing_session:
                    competing_session.add(
                        Announcement(
                            announcement_id=raw.announcement_id,
                            stock_code=raw.stock_code,
                            stock_name=raw.stock_name,
                            exchange=raw.exchange,
                            title=raw.title,
                            publish_date=raw.publish_date,
                            announcement_time=self._stored_datetime(raw.announcement_time),
                            pdf_url=raw.pdf_url,
                            strike_price=18.3,
                            is_eligible=True,
                            parse_status="pending",
                            is_latest_of_day=True,
                            latest_key="002947|2026-04-08",
                        )
                    )
                    await original_commit(competing_session)

        with (
            patch(
                "src.services.announcement_ingest_service.AsyncSessionLocal",
                self._service_session_factory_with_flag("inject_same_ann_conflict"),
            ),
            patch.object(AsyncSession, "commit", new=inject_reparse_conflict),
            patch.object(AsyncSession, "rollback", new=inject_reparse_conflict_rollback),
        ):
            result = await self.service.ingest_announcement(
                raw=raw,
                strike_price=22.7,
                parse_status="success",
                force_reparse=True,
            )

        announcements = await self._fetch_announcements()
        watch = await self._fetch_watch()

        self.assertEqual(result.action, "updated")
        self.assertTrue(result.watch_target_updated)
        self.assertTrue(announcements["same-draft"].is_latest_of_day)
        self.assertEqual(announcements["same-draft"].latest_key, "002947|2026-04-08")
        self.assertEqual(announcements["same-draft"].strike_price, 22.7)
        self.assertEqual(announcements["same-draft"].parse_status, "success")
        self.assertIsNotNone(watch)
        self.assertEqual(watch.strike_price, 22.7)


if __name__ == "__main__":
    unittest.main()
