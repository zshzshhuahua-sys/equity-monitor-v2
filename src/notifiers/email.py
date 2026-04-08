"""
邮件通知模块
支持SMTP邮件发送
"""
import csv
import html
import io
import os
import smtplib
import logging
from pathlib import Path
from datetime import date
from email.encoders import encode_base64
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import List, Optional
from dataclasses import dataclass
import asyncio

from ..database import AsyncSessionLocal
from ..database.models import EmailLog


logger = logging.getLogger(__name__)


@dataclass
class EmailConfig:
    """邮件配置"""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    use_tls: bool = True
    from_name: str = "股权激励监控"
    recipients: List[str] = None
    
    def __post_init__(self):
        if self.recipients is None:
            self.recipients = []


@dataclass
class EmailNotificationData:
    """邮件通知数据"""
    subject: str
    html_body: str
    plain_body: str = ""
    symbols: List[str] = None
    notification_type: str = "generic"
    job_id: Optional[str] = None
    crawl_date: Optional[str] = None
    
    def __post_init__(self):
        if self.symbols is None:
            self.symbols = []


class EmailNotifier:
    """邮件通知器"""
    
    def __init__(self, config: Optional[EmailConfig] = None):
        self._file_env = self._load_env_file_values()
        self._config = config or self._load_config()
        self._enabled = bool(self._config.smtp_user and self._config.recipients)

    def _load_env_file_values(self) -> dict[str, str]:
        """兜底读取项目 .env，避免非 Docker 运行时邮件模块拿不到配置。"""
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if not env_path.exists():
            return {}

        values: dict[str, str] = {}
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                values[key] = value
        except OSError as exc:
            logger.warning("读取 .env 失败，已忽略: %s", exc)
        return values

    def _get_env(self, key: str, default: str = "") -> str:
        if key in os.environ:
            return os.environ[key]
        return self._file_env.get(key, default)
    
    def _load_config(self) -> EmailConfig:
        """从环境变量加载配置"""
        smtp_password = self._load_secret("SMTP_PASSWORD")
        return EmailConfig(
            smtp_host=self._get_env("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=int(self._get_env("SMTP_PORT", "587")),
            smtp_user=self._get_env("SMTP_USER", ""),
            smtp_password=smtp_password,
            use_tls=self._get_env("SMTP_USE_TLS", "true").lower() == "true",
            from_name=self._get_env("SMTP_FROM_NAME", "股权激励监控"),
            recipients=[
                r.strip() for r in self._get_env("SMTP_RECIPIENTS", "").split(",")
                if r.strip()
            ]
        )

    def _load_secret(self, env_name: str) -> str:
        """
        优先从 *_FILE 读取敏感信息，便于接入 Docker secret / 挂载文件。
        文件不存在或读取失败时回退到普通环境变量。
        """
        secret_file = self._get_env(f"{env_name}_FILE", "").strip()
        if secret_file:
            try:
                return Path(secret_file).read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.warning("读取 %s 失败，已回退到环境变量: %s", f"{env_name}_FILE", exc)
        return self._get_env(env_name, "")

    def get_status(self) -> dict[str, object]:
        """返回脱敏后的邮件配置状态，便于健康检查与排障。"""
        issues: list[str] = []
        if not self._config.smtp_host:
            issues.append("missing_smtp_host")
        if not self._config.smtp_user:
            issues.append("missing_smtp_user")
        if not self._config.smtp_password:
            issues.append("missing_smtp_password")
        if not self._config.recipients:
            issues.append("missing_recipients")

        return {
            "enabled": self.is_enabled(),
            "smtp_host": self._config.smtp_host,
            "smtp_port": self._config.smtp_port,
            "smtp_user": self._config.smtp_user,
            "has_password": bool(self._config.smtp_password),
            "recipients_count": len(self._config.recipients),
            "issues": issues,
        }

    def _normalize_exception_message(self, exc: Exception) -> str:
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            return (
                f"SMTP authentication failed ({exc.smtp_code}): "
                "请检查/重置 iCloud app 专用密码；如果 Apple 账户主密码近期变更，旧 app 密码通常会失效。"
            )
        return str(exc)
    
    def is_enabled(self) -> bool:
        """检查邮件通知是否可用"""
        return self._enabled
    
    def _format_from(self) -> str:
        """
        构造 From 头。
        iCloud SMTP 会拒绝非 ASCII display name（RFC 6531），故含中文时回退到纯地址。
        """
        name = self._config.from_name
        # 非 ASCII 字符 → iCloud 拒绝，回退为纯邮箱地址
        if name and not name.encode('utf-8').isascii():
            return self._config.smtp_user
        return formataddr((name, self._config.smtp_user))

    def _create_message(self, data: EmailNotificationData) -> MIMEMultipart:
        """创建邮件消息"""
        msg = MIMEMultipart('alternative')
        msg['Subject'] = data.subject
        msg['From'] = self._format_from()
        msg['To'] = ", ".join(self._config.recipients)
        
        # 添加纯文本版本
        if data.plain_body:
            msg.attach(MIMEText(data.plain_body, 'plain', 'utf-8'))
        
        # 添加HTML版本
        msg.attach(MIMEText(data.html_body, 'html', 'utf-8'))
        
        return msg

    async def _save_email_log_async(
        self,
        *,
        notification_type: str,
        status: str,
        subject: str,
        job_id: Optional[str] = None,
        crawl_date: Optional[str] = None,
        stock_symbol: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        async with AsyncSessionLocal() as session:
            session.add(EmailLog(
                notification_type=notification_type,
                status=status,
                job_id=job_id,
                crawl_date=crawl_date,
                stock_symbol=stock_symbol,
                subject=subject[:255],
                recipients=",".join(self._config.recipients)[:1000],
                error_message=(error_message or "")[:1000] or None,
            ))
            await session.commit()

    def _save_email_log(
        self,
        *,
        notification_type: str,
        status: str,
        subject: str,
        job_id: Optional[str] = None,
        crawl_date: Optional[str] = None,
        stock_symbol: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        try:
            coroutine = self._save_email_log_async(
                notification_type=notification_type,
                status=status,
                subject=subject,
                job_id=job_id,
                crawl_date=crawl_date,
                stock_symbol=stock_symbol,
                error_message=error_message,
            )
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(coroutine)
            else:
                loop.create_task(coroutine)
        except Exception as exc:
            logger.warning("邮件发送日志落库失败: %s", exc)

    def _deliver_message(
        self,
        msg: MIMEMultipart,
        *,
        subject: str,
        notification_type: str,
        job_id: Optional[str] = None,
        crawl_date: Optional[str] = None,
        stock_symbol: Optional[str] = None,
    ) -> bool:
        if not self.is_enabled():
            reason = "邮件通知未启用，跳过"
            print(f"[邮件通知] {reason}")
            self._save_email_log(
                notification_type=notification_type,
                status="skipped",
                subject=subject,
                job_id=job_id,
                crawl_date=crawl_date,
                stock_symbol=stock_symbol,
                error_message=reason,
            )
            return False

        if not self._config.recipients:
            reason = "未配置收件人，跳过"
            print(f"[邮件通知] {reason}")
            self._save_email_log(
                notification_type=notification_type,
                status="skipped",
                subject=subject,
                job_id=job_id,
                crawl_date=crawl_date,
                stock_symbol=stock_symbol,
                error_message=reason,
            )
            return False

        try:
            with smtplib.SMTP(self._config.smtp_host, self._config.smtp_port, timeout=10) as server:
                if self._config.use_tls:
                    server.starttls()

                if self._config.smtp_user and self._config.smtp_password:
                    server.login(self._config.smtp_user, self._config.smtp_password)

                server.send_message(msg)

            print(f"[邮件通知] 发送成功: {subject}")
            self._save_email_log(
                notification_type=notification_type,
                status="success",
                subject=subject,
                job_id=job_id,
                crawl_date=crawl_date,
                stock_symbol=stock_symbol,
            )
            return True

        except Exception as e:
            error_message = self._normalize_exception_message(e)
            print(f"[邮件通知] 发送失败: {error_message}")
            self._save_email_log(
                notification_type=notification_type,
                status="failed",
                subject=subject,
                job_id=job_id,
                crawl_date=crawl_date,
                stock_symbol=stock_symbol,
                error_message=error_message,
            )
            return False
    
    def send(self, data: EmailNotificationData) -> bool:
        """
        发送邮件通知
        
        Args:
            data: 邮件数据
        
        Returns:
            是否发送成功
        """
        msg = self._create_message(data)
        return self._deliver_message(
            msg,
            subject=data.subject,
            notification_type=data.notification_type,
            job_id=data.job_id,
            crawl_date=data.crawl_date,
            stock_symbol=data.symbols[0] if data.symbols else None,
        )
    
    async def send_async(self, data: EmailNotificationData) -> bool:
        """异步发送邮件"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.send, data)

    def _build_csv_attachment(self, rows: List[dict], filename: str) -> MIMEBase:
        """构建 CSV 附件"""
        output = io.StringIO()
        if not rows:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload('')
            encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename={filename}')
            return part

        fieldnames = ['stock_code', 'stock_name', 'publish_date', 'title',
                       'is_eligible', 'strike_price', 'option_ratio',
                       'incentive_object_count', 'option_allocation']
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
        payload = output.getvalue().encode('utf-8-sig')

        part = MIMEBase('application', 'octet-stream')
        part.set_payload(payload)
        encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename={filename}')
        return part

    def send_crawl_report(
        self,
        crawl_date: date,
        stats: dict,
        new_announcements: List[dict],
        job_id: Optional[str] = None,
    ) -> bool:
        """
        发送每日爬取报告邮件

        Args:
            crawl_date: 爬取日期
            stats: 爬取统计 {'total_fetched', 'new_added', 'parse_success', ...}
            new_announcements: 新增公告列表（dict）
        """
        date_str = crawl_date.strftime("%Y-%m-%d")
        new_count = stats.get("new_added", 0)

        msg = MIMEMultipart('mixed')
        msg['From'] = self._format_from()
        msg['To'] = ", ".join(self._config.recipients)

        if new_count > 0:
            subject = f"股权激励监控 - {date_str} 新增 {new_count} 条公告"
            rows_html = ""
            for ann in new_announcements:
                eligible_str = "✅" if ann.get("is_eligible") else "❌"
                strike = f"¥{ann['strike_price']:.4f}" if ann.get("strike_price") else "—"
                # 用户输入字段需转义防 HTML 注入
                stock_code = html.escape(str(ann.get('stock_code', '')))
                stock_name = html.escape(str(ann.get('stock_name', '')))
                publish_date = html.escape(str(ann.get('publish_date', '')))
                title = html.escape(str(ann.get('title', ''))[:40])
                rows_html += f"""
                <tr>
                    <td style="padding:8px;border-bottom:1px solid #eee">{stock_code}</td>
                    <td style="padding:8px;border-bottom:1px solid #eee">{stock_name}</td>
                    <td style="padding:8px;border-bottom:1px solid #eee">{publish_date}</td>
                    <td style="padding:8px;border-bottom:1px solid #eee">{title}</td>
                    <td style="padding:8px;border-bottom:1px solid #eee;text-align:center">{eligible_str}</td>
                    <td style="padding:8px;border-bottom:1px solid #eee;text-align:right">{strike}</td>
                </tr>"""

            html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9f9f9; padding: 20px; }}
        table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; }}
        th {{ background: #f1f1f1; padding: 10px 8px; text-align: left; font-size: 13px; color: #555; }}
        .footer {{ color: #999; font-size: 12px; margin-top: 20px; text-align: center; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2 style="margin:0;">📋 股权激励监控日报</h2>
            <p style="margin:5px 0 0">{date_str} 新增 {new_count} 条公告</p>
        </div>
        <div class="content">
            <table>
                <thead>
                    <tr>
                        <th>代码</th><th>名称</th><th>日期</th><th>标题</th><th>符合条件</th><th>行权价</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
            <p class="footer">股权激励监控面板 · 每日自动更新</p>
        </div>
    </div>
</body>
</html>"""
            plain_body = f"股权激励监控日报 {date_str}\n新增 {new_count} 条公告\n\n股票代码 | 名称 | 日期 | 标题 | 符合条件 | 行权价\n" + "\n".join(
                "{} | {} | {} | {} | {} | {}".format(
                    a.get('stock_code', ''),
                    a.get('stock_name', ''),
                    a.get('publish_date', ''),
                    a.get('title', '')[:30],
                    '是' if a.get('is_eligible') else '否',
                    f"¥{a['strike_price']:.4f}" if a.get('strike_price') else '—',
                )
                for a in new_announcements
            )
        else:
            subject = f"股权激励监控 - {date_str} 今日无新增"
            html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; }}
        .header {{ background: linear-gradient(135deg, #6b7280 0%, #4b5563 100%); color: white; padding: 20px; border-radius: 8px 8px 0 0; text-align: center; }}
        .content {{ background: #f9f9f9; padding: 40px 20px; text-align: center; }}
        .empty {{ font-size: 48px; margin-bottom: 10px; }}
        .footer {{ color: #999; font-size: 12px; margin-top: 20px; text-align: center; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2 style="margin:0;">股权激励监控日报</h2>
            <p style="margin:5px 0 0">{date_str}</p>
        </div>
        <div class="content">
            <div class="empty">📭</div>
            <p style="font-size:18px;color:#555;margin:0;">今日无新增公告</p>
            <p style="color:#999;margin-top:10px;">数据来源：巨潮资讯网</p>
        </div>
        <p class="footer">股权激励监控面板 · 每日自动更新</p>
    </div>
</body>
</html>"""
            plain_body = f"股权激励监控日报\n{date_str}\n今日无新增公告\n\n数据来源：巨潮资讯网"

        msg['Subject'] = subject
        msg.attach(MIMEText(plain_body, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        # 有新增公告时附加 CSV
        if new_count > 0 and new_announcements:
            csv_filename = f"equity_incentive_{date_str}.csv"
            msg.attach(self._build_csv_attachment(new_announcements, csv_filename))

        return self._deliver_message(
            msg,
            subject=subject,
            notification_type="crawl_report",
            job_id=job_id,
            crawl_date=date_str,
        )

    async def send_crawl_report_async(
        self,
        crawl_date: date,
        stats: dict,
        new_announcements: List[dict],
        job_id: Optional[str] = None,
    ) -> bool:
        """异步发送爬取报告"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.send_crawl_report, crawl_date, stats, new_announcements, job_id
        )
    
    def send_alert(self, symbol: str, full_code: str, name: str, 
                   current_price: float, strike_price: float, 
                   diff_percent: float, alert_level: str) -> bool:
        """
        发送预警邮件
        
        Args:
            symbol: 股票代码
            full_code: 完整代码
            name: 股票名称
            current_price: 当前价格
            strike_price: 执行价格
            diff_percent: 价差百分比
            alert_level: 预警级别
        
        Returns:
            是否发送成功
        """
        alert_emoji = {
            "normal": "🟢",
            "watch": "🟡",
            "warning": "🟠",
            "critical": "🔴"
        }
        
        emoji = alert_emoji.get(alert_level, "⚠️")
        
        subject = f"{emoji} 股权激励预警 - {full_code} ({name}) - {diff_percent:+.2f}%"
        
        plain_body = f"""
股票代码: {full_code}
股票名称: {name}
当前价格: ¥{current_price:.2f}
执行价格: ¥{strike_price:.2f}
价差金额: ¥{(current_price - strike_price):+.2f}
价差比例: {diff_percent:+.2f}%
预警级别: {alert_level.upper()}

请及时登录监控面板查看详情。
"""
        
        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9f9f9; padding: 20px; }}
        .stock-info {{ background: white; padding: 15px; border-radius: 8px; margin: 10px 0; }}
        .label {{ color: #666; font-size: 14px; }}
        .value {{ color: #333; font-size: 18px; font-weight: bold; }}
        .alert-critical {{ border-left: 4px solid #ef4444; }}
        .alert-warning {{ border-left: 4px solid #f97316; }}
        .alert-watch {{ border-left: 4px solid #f59e0b; }}
        .alert-normal {{ border-left: 4px solid #10b981; }}
        .diff-positive {{ color: #10b981; }}
        .diff-negative {{ color: #ef4444; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2 style="margin: 0;">{emoji} 股权激励预警</h2>
        </div>
        <div class="content">
            <div class="stock-info alert-{alert_level}">
                <p class="label">股票代码</p>
                <p class="value">{full_code}</p>
                <p class="label">股票名称</p>
                <p class="value">{name}</p>
            </div>
            <div class="stock-info">
                <table style="width: 100%; border-collapse: collapse;">
                    <tr>
                        <td style="padding: 8px 0;">
                            <p class="label">当前价格</p>
                            <p class="value">¥{current_price:.2f}</p>
                        </td>
                        <td style="padding: 8px 0;">
                            <p class="label">执行价格</p>
                            <p class="value">¥{strike_price:.2f}</p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0;">
                            <p class="label">价差金额</p>
                            <p class="value {'diff-positive' if diff_percent >= 0 else 'diff-negative'}">¥{(current_price - strike_price):+.2f}</p>
                        </td>
                        <td style="padding: 8px 0;">
                            <p class="label">价差比例</p>
                            <p class="value {'diff-positive' if diff_percent >= 0 else 'diff-negative'}">{diff_percent:+.2f}%</p>
                        </td>
                    </tr>
                </table>
            </div>
            <p style="color: #666; font-size: 14px;">
                预警级别: <strong>{alert_level.upper()}</strong>
            </p>
            <p style="color: #999; font-size: 12px; margin-top: 20px;">
                来自股权激励监控面板
            </p>
        </div>
    </div>
</body>
</html>
"""
        
        return self.send(EmailNotificationData(
            subject=subject,
            html_body=html_body,
            plain_body=plain_body,
            symbols=[symbol],
            notification_type="alert",
        ))


# 全局邮件通知器实例
email_notifier = EmailNotifier()
