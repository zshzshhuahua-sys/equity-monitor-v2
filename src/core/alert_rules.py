"""
预警规则引擎
支持全局默认 + 单股票自定义阈值
"""
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dataclasses import dataclass
from enum import Enum

from ..config import settings
from .diff_calculator import AlertLevel, DiffCalculator


class AlertType(Enum):
    """预警类型"""
    THRESHOLD_BREACH = "threshold_breach"  # 阈值突破
    PRICE_DROP = "price_drop"              # 价格下跌
    PRICE_SPIKE = "price_spike"            # 价格暴涨


@dataclass
class AlertRule:
    """预警规则"""
    symbol: str
    watch_threshold: float      # 关注阈值
    warning_threshold: float    # 警告阈值
    critical_threshold: float   # 严重阈值
    is_custom: bool             # 是否为自定义规则


class AlertRuleEngine:
    """预警规则引擎"""
    
    def __init__(self):
        self.global_thresholds = {
            AlertLevel.WATCH: settings.alert.thresholds.watch,
            AlertLevel.WARNING: settings.alert.thresholds.warning,
            AlertLevel.CRITICAL: settings.alert.thresholds.critical
        }
        self._custom_rules: Dict[str, AlertRule] = {}
    
    def get_rule_for_stock(self, symbol: str, 
                           custom_threshold: Optional[float] = None) -> AlertRule:
        """
        获取股票的预警规则
        
        优先级：
        1. 股票自定义阈值（custom_threshold）
        2. 全局默认阈值
        
        Args:
            symbol: 股票代码
            custom_threshold: 自定义阈值（可选）
        
        Returns:
            AlertRule
        """
        # 如果设置了自定义阈值，使用统一的阈值级别
        if custom_threshold is not None and custom_threshold > 0:
            return AlertRule(
                symbol=symbol,
                watch_threshold=custom_threshold * 0.5,      # 50% of threshold
                warning_threshold=custom_threshold * 0.8,    # 80% of threshold
                critical_threshold=custom_threshold,          # 100% of threshold
                is_custom=True
            )
        
        # 使用全局默认阈值
        return AlertRule(
            symbol=symbol,
            watch_threshold=self.global_thresholds[AlertLevel.WATCH],
            warning_threshold=self.global_thresholds[AlertLevel.WARNING],
            critical_threshold=self.global_thresholds[AlertLevel.CRITICAL],
            is_custom=False
        )
    
    def evaluate(self, diff_percent: float, rule: AlertRule) -> tuple[AlertLevel, float]:
        """
        根据规则评估预警级别
        
        Args:
            diff_percent: 价差百分比（小数形式，如 0.15 表示 15%）
            rule: 预警规则
        
        Returns:
            (预警级别, 触发阈值)
        """
        abs_percent = abs(diff_percent)
        
        if abs_percent >= rule.critical_threshold:
            return AlertLevel.CRITICAL, rule.critical_threshold
        elif abs_percent >= rule.warning_threshold:
            return AlertLevel.WARNING, rule.warning_threshold
        elif abs_percent >= rule.watch_threshold:
            return AlertLevel.WATCH, rule.watch_threshold
        else:
            return AlertLevel.NORMAL, 0.0


class AlertCooldownManager:
    """预警冷却管理器"""
    
    def __init__(self, cooldown_minutes: int = 30):
        """
        初始化冷却管理器
        
        Args:
            cooldown_minutes: 冷却时间（分钟）
        """
        self.cooldown_minutes = cooldown_minutes
        self._last_alerts: Dict[str, datetime] = {}
    
    def is_in_cooldown(self, symbol: str) -> bool:
        """
        检查股票是否处于冷却期
        
        Args:
            symbol: 股票代码
        
        Returns:
            是否在冷却期
        """
        if symbol not in self._last_alerts:
            return False
        
        last_time = self._last_alerts[symbol]
        elapsed = datetime.utcnow() - last_time
        
        return elapsed < timedelta(minutes=self.cooldown_minutes)
    
    def record_alert(self, symbol: str):
        """
        记录预警时间
        
        Args:
            symbol: 股票代码
        """
        self._last_alerts[symbol] = datetime.utcnow()
    
    def get_remaining_cooldown(self, symbol: str) -> int:
        """
        获取剩余冷却时间（秒）
        
        Args:
            symbol: 股票代码
        
        Returns:
            剩余冷却时间（秒），如果不在冷却期返回0
        """
        if not self.is_in_cooldown(symbol):
            return 0
        
        last_time = self._last_alerts[symbol]
        elapsed = datetime.utcnow() - last_time
        remaining = timedelta(minutes=self.cooldown_minutes) - elapsed
        
        return max(0, int(remaining.total_seconds()))
    
    def clear_cooldown(self, symbol: Optional[str] = None):
        """
        清除冷却记录
        
        Args:
            symbol: 股票代码，如果为None则清除所有
        """
        if symbol:
            self._last_alerts.pop(symbol, None)
        else:
            self._last_alerts.clear()


class AlertEngine:
    """预警引擎"""
    
    def __init__(self):
        self.rule_engine = AlertRuleEngine()
        self.cooldown_manager = AlertCooldownManager(
            cooldown_minutes=settings.alert.cooldown_minutes
        )
    
    def should_alert(self, symbol: str, diff_percent: float,
                     custom_threshold: Optional[float] = None) -> tuple[bool, AlertLevel, float]:
        """
        判断是否应该触发预警
        
        Args:
            symbol: 股票代码
            diff_percent: 价差百分比
            custom_threshold: 自定义阈值
        
        Returns:
            (是否应该预警, 预警级别, 触发阈值)
        """
        # 检查冷却期
        if self.cooldown_manager.is_in_cooldown(symbol):
            return False, AlertLevel.NORMAL, 0.0
        
        # 获取规则
        rule = self.rule_engine.get_rule_for_stock(symbol, custom_threshold)
        
        # 评估预警级别
        level, threshold = self.rule_engine.evaluate(diff_percent, rule)
        
        # 只有达到关注级别以上才触发
        should_trigger = level != AlertLevel.NORMAL
        
        if should_trigger:
            self.cooldown_manager.record_alert(symbol)
        
        return should_trigger, level, threshold


# 全局预警引擎实例
alert_engine = AlertEngine()
