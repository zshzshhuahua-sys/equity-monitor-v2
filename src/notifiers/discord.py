"""
Discord Webhook 通知模块
"""
import logging
import asyncio
from datetime import date
from typing import Optional, List
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class DiscordEmbedField:
    """Discord Embed 字段"""
    name: str
    value: str
    inline: bool = False


@dataclass
class DiscordEmbed:
    """Discord Embed"""
    title: str
    description: str
    color: int = 5814783  # 蓝色
    fields: Optional[List[DiscordEmbedField]] = None
    footer_text: Optional[str] = None


class DiscordNotifier:
    """Discord Webhook 通知器"""

    def __init__(self, webhook_url: Optional[str] = None):
        self._webhook_url = webhook_url
        self._enabled = bool(webhook_url)

    def _load_config(self) -> bool:
        """从环境变量加载配置"""
        import os
        from pathlib import Path

        # 优先从环境变量读取
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

        # 兜底读取项目 .env 文件
        if not webhook_url:
            env_path = Path(__file__).resolve().parents[2] / ".env"
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("DISCORD_WEBHOOK_URL="):
                        webhook_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break

        self._webhook_url = webhook_url
        self._enabled = bool(webhook_url)
        return self._enabled

    def is_enabled(self) -> bool:
        """检查通知是否可用"""
        if not self._webhook_url:
            self._load_config()
        return self._enabled

    def get_status(self) -> dict:
        """返回配置状态"""
        return {
            "enabled": self.is_enabled(),
            "webhook_url_set": bool(self._webhook_url),
            "webhook_url_preview": (
                f"{self._webhook_url[:40]}..."
                if self._webhook_url and len(self._webhook_url) > 40
                else self._webhook_url
            ) if self._webhook_url else None,
        }

    def _build_payload(self, embed: DiscordEmbed) -> dict:
        """构建 Discord payload"""
        payload = {
            "embeds": [{
                "title": embed.title,
                "description": embed.description,
                "color": embed.color,
            }]
        }

        if embed.fields:
            payload["embeds"][0]["fields"] = [
                {"name": f.name, "value": f.value, "inline": f.inline}
                for f in embed.fields
            ]

        if embed.footer_text:
            payload["embeds"][0]["footer"] = {"text": embed.footer_text}

        return payload

    def _send_webhook(self, payload: dict) -> bool:
        """发送 Webhook 请求"""
        if not self.is_enabled():
            logger.warning("Discord 通知未启用，跳过")
            return False

        try:
            with httpx.Client(timeout=10) as client:
                response = client.post(
                    self._webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                if response.status_code == 204:
                    logger.info("Discord 通知发送成功")
                    return True
                else:
                    logger.error(
                        "Discord 通知发送失败: HTTP %s - %s",
                        response.status_code,
                        response.text,
                    )
                    return False
        except httpx.ConnectTimeout:
            logger.error("Discord 通知连接超时")
            return False
        except httpx.ReadTimeout:
            logger.error("Discord 通知读取超时")
            return False
        except TimeoutError:
            logger.error("Discord 通知超时")
            return False
        except Exception as e:
            logger.exception("Discord 通知发送异常: %s", e)
            return False

    def send(self, embed: DiscordEmbed) -> bool:
        """发送 Embed 消息"""
        payload = self._build_payload(embed)
        return self._send_webhook(payload)

    def send_crawl_report(
        self,
        crawl_date: date,
        stats: dict,
        new_announcements: List[dict],
    ) -> bool:
        """
        发送每日爬取报告

        Args:
            crawl_date: 爬取日期
            stats: 爬取统计
            new_announcements: 新增公告列表
        """
        date_str = crawl_date.strftime("%Y-%m-%d")
        # 只保留符合条件的公告
        eligible_anns = [a for a in new_announcements if a.get("is_eligible")]
        actual_count = len(eligible_anns)
        total_count = len(new_announcements)

        if actual_count > 0:
            # 有新增公告
            title = f"📋 股权激励监控日报 - {date_str}"
            description = f"✅ 新增 **{actual_count}** 条符合条件的公告"

            # 构建字段
            fields = []
            for ann in eligible_anns[:10]:  # 最多显示10条
                eligible_str = "✅" if ann.get("is_eligible") else "❌"
                strike = f"¥{ann['strike_price']:.4f}" if ann.get("strike_price") else "—"
                name = ann.get("stock_name", "")[:8]
                code = ann.get("stock_code", "")
                fields.append(DiscordEmbedField(
                    name=f"{eligible_str} {name} ({code})",
                    value=f"行权价: {strike}",
                    inline=True,
                ))

            # 如果超过10条，添加提示
            if len(eligible_anns) > 10:
                fields.append(DiscordEmbedField(
                    name="...",
                    value=f"还有 {len(eligible_anns) - 10} 条公告",
                    inline=False,
                ))

            embed = DiscordEmbed(
                title=title,
                description=description,
                color=5814783,  # 蓝色
                fields=fields,
                footer_text="股权激励监控面板 · 每日自动更新",
            )
        else:
            # 无符合条件的公告
            if total_count > 0:
                # 有入库但不满足条件
                description = f"📭 今日入库 {total_count} 条公告，均不符合条件"
            else:
                description = "📭 今日无新增公告"
            embed = DiscordEmbed(
                title=f"📋 股权激励监控日报 - {date_str}",
                description=description,
                color=8603937,  # 灰色
                footer_text="股权激励监控面板 · 每日自动更新",
            )

        return self.send(embed)

    def send_alert(
        self,
        symbol: str,
        full_code: str,
        name: str,
        current_price: float,
        strike_price: float,
        diff_percent: float,
        alert_level: str,
    ) -> bool:
        """发送预警通知"""
        alert_emoji = {
            "normal": "🟢",
            "watch": "🟡",
            "warning": "🟠",
            "critical": "🔴",
        }
        emoji = alert_emoji.get(alert_level, "⚠️")

        title = f"{emoji} 股权激励预警 - {full_code}"
        description = f"**{name}**\n价差比例: {diff_percent:+.2f}%"

        fields = [
            DiscordEmbedField(name="当前价格", value=f"¥{current_price:.2f}", inline=True),
            DiscordEmbedField(name="执行价格", value=f"¥{strike_price:.2f}", inline=True),
            DiscordEmbedField(name="价差金额", value=f"¥{current_price - strike_price:+.2f}", inline=True),
            DiscordEmbedField(name="预警级别", value=alert_level.upper(), inline=False),
        ]

        color_map = {
            "normal": 32768,     # 绿色
            "watch": 16776960,  # 黄色
            "warning": 16744448,  # 橙色
            "critical": 13369344,  # 红色
        }
        color = color_map.get(alert_level, 5814783)

        embed = DiscordEmbed(
            title=title,
            description=description,
            color=color,
            fields=fields,
        )

        return self.send(embed)


# 全局通知器实例
discord_notifier = DiscordNotifier()
