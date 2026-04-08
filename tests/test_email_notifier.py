import asyncio
import os
import smtplib
import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.database.models import Base, EmailLog
from src.notifiers import email as email_module
from src.notifiers.email import EmailConfig, EmailNotifier


class FakeSMTP:
    def __init__(self, host, port, timeout=10):
        self.host = host
        self.port = port
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        return None

    def login(self, user, password):
        return None

    def send_message(self, msg):
        return None


class FailingSMTP(FakeSMTP):
    def send_message(self, msg):
        raise RuntimeError("smtp boom")


class AuthFailingSMTP(FakeSMTP):
    def login(self, user, password):
        raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Error: authentication failed")


async def _create_schema(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _fetch_logs(session_factory):
    async with session_factory() as session:
        result = await session.execute(select(EmailLog).order_by(EmailLog.id.asc()))
        return result.scalars().all()


class EmailNotifierTests(unittest.TestCase):
    def test_load_config_falls_back_to_env_file_values(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            EmailNotifier,
            "_load_env_file_values",
            return_value={
                "SMTP_HOST": "smtp.mail.me.com",
                "SMTP_PORT": "587",
                "SMTP_USER": "bot@icloud.com",
                "SMTP_PASSWORD": "secret-from-env-file",
                "SMTP_RECIPIENTS": "ops@example.com",
            },
        ):
            notifier = EmailNotifier()

        self.assertEqual(notifier._config.smtp_host, "smtp.mail.me.com")
        self.assertEqual(notifier._config.smtp_user, "bot@icloud.com")
        self.assertEqual(notifier._config.smtp_password, "secret-from-env-file")
        self.assertEqual(notifier._config.recipients, ["ops@example.com"])

    def test_load_config_reads_password_from_file(self):
        secret_file = os.path.abspath("tests/.smtp_password_test")
        with open(secret_file, "w", encoding="utf-8") as fh:
            fh.write("secret-from-file\n")

        self.addCleanup(lambda: os.path.exists(secret_file) and os.remove(secret_file))

        with patch.dict(
            os.environ,
            {
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PORT": "587",
                "SMTP_USER": "bot@example.com",
                "SMTP_PASSWORD_FILE": secret_file,
                "SMTP_RECIPIENTS": "ops@example.com",
            },
            clear=False,
        ):
            os.environ.pop("SMTP_PASSWORD", None)
            notifier = EmailNotifier()

        self.assertEqual(notifier._config.smtp_password, "secret-from-file")

    def test_send_crawl_report_logs_success(self):
        db_path = os.path.abspath("tests/email_logs_success.db")
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        asyncio.run(_create_schema(engine))

        self.addCleanup(lambda: asyncio.run(engine.dispose()))
        self.addCleanup(lambda: os.path.exists(db_path) and os.remove(db_path))

        notifier = EmailNotifier(
            EmailConfig(
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_user="bot@example.com",
                smtp_password="secret",
                recipients=["ops@example.com"],
            )
        )

        with patch.object(email_module, "AsyncSessionLocal", session_factory), patch.object(
            email_module.smtplib, "SMTP", FakeSMTP
        ):
            result = notifier.send_crawl_report(
                crawl_date=date(2026, 4, 2),
                stats={"new_added": 0},
                new_announcements=[],
                job_id="nightly_crawl",
            )

        logs = asyncio.run(_fetch_logs(session_factory))

        self.assertTrue(result)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].notification_type, "crawl_report")
        self.assertEqual(logs[0].status, "success")
        self.assertEqual(logs[0].job_id, "nightly_crawl")
        self.assertEqual(logs[0].crawl_date, "2026-04-02")
        self.assertEqual(logs[0].subject, "股权激励监控 - 2026-04-02 今日无新增")

    def test_send_crawl_report_logs_failure(self):
        db_path = os.path.abspath("tests/email_logs_failed.db")
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        asyncio.run(_create_schema(engine))

        self.addCleanup(lambda: asyncio.run(engine.dispose()))
        self.addCleanup(lambda: os.path.exists(db_path) and os.remove(db_path))

        notifier = EmailNotifier(
            EmailConfig(
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_user="bot@example.com",
                smtp_password="secret",
                recipients=["ops@example.com"],
            )
        )

        with patch.object(email_module, "AsyncSessionLocal", session_factory), patch.object(
            email_module.smtplib, "SMTP", FailingSMTP
        ):
            result = notifier.send_crawl_report(
                crawl_date=date(2026, 4, 2),
                stats={"new_added": 0},
                new_announcements=[],
                job_id="nightly_crawl",
            )

        logs = asyncio.run(_fetch_logs(session_factory))

        self.assertFalse(result)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].notification_type, "crawl_report")
        self.assertEqual(logs[0].status, "failed")
        self.assertEqual(logs[0].job_id, "nightly_crawl")
        self.assertIn("smtp boom", logs[0].error_message or "")

    def test_send_crawl_report_logs_auth_failure_with_actionable_message(self):
        db_path = os.path.abspath("tests/email_logs_auth_failed.db")
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        asyncio.run(_create_schema(engine))

        self.addCleanup(lambda: asyncio.run(engine.dispose()))
        self.addCleanup(lambda: os.path.exists(db_path) and os.remove(db_path))

        notifier = EmailNotifier(
            EmailConfig(
                smtp_host="smtp.mail.me.com",
                smtp_port=587,
                smtp_user="bot@icloud.com",
                smtp_password="secret",
                recipients=["ops@example.com"],
            )
        )

        with patch.object(email_module, "AsyncSessionLocal", session_factory), patch.object(
            email_module.smtplib, "SMTP", AuthFailingSMTP
        ):
            result = notifier.send_crawl_report(
                crawl_date=date(2026, 4, 2),
                stats={"new_added": 0},
                new_announcements=[],
                job_id="nightly_crawl",
            )

        logs = asyncio.run(_fetch_logs(session_factory))

        self.assertFalse(result)
        self.assertEqual(logs[0].status, "failed")
        self.assertIn("iCloud app 专用密码", logs[0].error_message or "")


if __name__ == "__main__":
    unittest.main()
