"""
配置管理
支持YAML配置文件和环境变量
"""
import os
from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "settings.yaml"


class AlertThresholds(BaseSettings):
    """预警阈值配置"""
    watch: float = Field(default=0.05, description="关注阈值，默认5%")
    warning: float = Field(default=0.10, description="警告阈值，默认10%")
    critical: float = Field(default=0.20, description="严重阈值，默认20%")


class MonitorConfig(BaseSettings):
    """监控配置"""
    interval_seconds: int = Field(default=30, description="轮询间隔，默认30秒")
    enabled: bool = Field(default=True, description="是否启用监控")
    trading_hours_only: bool = Field(default=True, description="仅交易时间监控")


class AlertConfig(BaseSettings):
    """预警配置"""
    enabled: bool = Field(default=True, description="是否启用预警")
    desktop_notification: bool = Field(default=True, description="桌面通知")
    email_notification: bool = Field(default=False, description="邮件通知")
    cooldown_minutes: int = Field(default=30, description="同一股票预警冷却时间（分钟）")
    thresholds: AlertThresholds = Field(default_factory=AlertThresholds)


class Settings(BaseSettings):
    """应用配置"""
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore'
    )
    
    # 应用信息
    app_name: str = Field(default="股权激励监控面板")
    app_version: str = Field(default="1.0.0")
    debug: bool = Field(default=False)
    
    # 服务器配置
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8001)
    
    # 模块配置
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)
    alert: AlertConfig = Field(default_factory=AlertConfig)
    
    # 数据库配置
    database_url: str = Field(default=f"sqlite+aiosqlite:///{PROJECT_ROOT}/data/equity_monitor.db")
    
    @classmethod
    def from_yaml(cls, yaml_path: Optional[Path] = None) -> "Settings":
        """从YAML文件加载配置"""
        import yaml
        
        if yaml_path is None:
            yaml_path = CONFIG_FILE
        
        if not yaml_path.exists():
            return cls()
        
        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                config_dict = yaml.safe_load(f)
            return cls(**config_dict) if config_dict else cls()
        except Exception as e:
            print(f"Warning: Failed to load config from {yaml_path}: {e}")
            return cls()
    
    def to_yaml(self, yaml_path: Optional[Path] = None):
        """保存配置到YAML文件"""
        import yaml
        
        if yaml_path is None:
            yaml_path = CONFIG_FILE
        
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        
        config_dict = self.model_dump()
        with open(yaml_path, 'w', encoding='utf-8') as f:
            yaml.dump(config_dict, f, allow_unicode=True, default_flow_style=False)


# 全局配置实例
settings = Settings.from_yaml()


# ============================
# 巨潮资讯网常量（爬虫专用）
# ============================

CNINFO_BASE_URL = "http://www.cninfo.com.cn"
CNINFO_ANNOUNCEMENT_URL = f"{CNINFO_BASE_URL}/new/hisAnnouncement/query"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive",
}
