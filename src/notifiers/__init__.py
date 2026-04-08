"""
通知模块初始化
"""
from .desktop import desktop_notifier, DesktopNotifier, NotificationData
from .discord import discord_notifier, DiscordNotifier, DiscordEmbed, DiscordEmbedField

__all__ = [
    "desktop_notifier",
    "DesktopNotifier",
    "NotificationData",
    "discord_notifier",
    "DiscordNotifier",
    "DiscordEmbed",
    "DiscordEmbedField",
]
