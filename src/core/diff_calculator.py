"""
价差计算器
计算市场价与执行价格的差异
"""
from dataclasses import dataclass
from typing import Optional, Dict
from enum import Enum


class AlertLevel(Enum):
    """预警级别"""
    NORMAL = "normal"       # 正常
    WATCH = "watch"         # 关注
    WARNING = "warning"     # 警告
    CRITICAL = "critical"   # 严重


@dataclass
class PriceDiff:
    """价差计算结果"""
    symbol: str
    full_code: str
    name: Optional[str]
    current_price: float
    strike_price: float
    diff_amount: float        # 价差金额（当前价 - 执行价）
    diff_percent: float       # 价差百分比
    alert_level: AlertLevel   # 预警级别
    is_profitable: bool       # 是否盈利（当前价 > 执行价）


class DiffCalculator:
    """价差计算器"""
    
    def __init__(self, watch_threshold: float = 0.05, 
                 warning_threshold: float = 0.10,
                 critical_threshold: float = 0.20):
        """
        初始化计算器
        
        Args:
            watch_threshold: 关注阈值（默认5%）
            warning_threshold: 警告阈值（默认10%）
            critical_threshold: 严重阈值（默认20%）
        """
        self.watch_threshold = watch_threshold
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
    
    def calculate(self, current_price: float, strike_price: float,
                  symbol: str = "", full_code: str = "", 
                  name: Optional[str] = None) -> PriceDiff:
        """
        计算价差
        
        Args:
            current_price: 当前市场价格
            strike_price: 执行价格（期权成本）
            symbol: 股票代码
            full_code: 完整代码
            name: 股票名称
        
        Returns:
            PriceDiff
        """
        # 计算价差金额
        diff_amount = current_price - strike_price
        
        # 计算价差百分比（相对于执行价）
        if strike_price > 0:
            diff_percent = (diff_amount / strike_price)
        else:
            diff_percent = 0.0
        
        # 确定预警级别（使用绝对值）
        abs_percent = abs(diff_percent)
        
        if abs_percent >= self.critical_threshold:
            alert_level = AlertLevel.CRITICAL
        elif abs_percent >= self.warning_threshold:
            alert_level = AlertLevel.WARNING
        elif abs_percent >= self.watch_threshold:
            alert_level = AlertLevel.WATCH
        else:
            alert_level = AlertLevel.NORMAL
        
        # 是否盈利
        is_profitable = diff_amount > 0
        
        return PriceDiff(
            symbol=symbol,
            full_code=full_code,
            name=name,
            current_price=round(current_price, 2),
            strike_price=round(strike_price, 2),
            diff_amount=round(diff_amount, 2),
            diff_percent=round(diff_percent * 100, 2),  # 转换为百分比显示
            alert_level=alert_level,
            is_profitable=is_profitable
        )
    
    def calculate_batch(self, prices: Dict[str, float], 
                        strike_prices: Dict[str, float],
                        names: Optional[Dict[str, str]] = None) -> Dict[str, PriceDiff]:
        """
        批量计算价差
        
        Args:
            prices: Dict[full_code, current_price]
            strike_prices: Dict[full_code, strike_price]
            names: Dict[full_code, name]
        
        Returns:
            Dict[full_code, PriceDiff]
        """
        results = {}
        names = names or {}
        
        for full_code, current_price in prices.items():
            if full_code in strike_prices:
                result = self.calculate(
                    current_price=current_price,
                    strike_price=strike_prices[full_code],
                    symbol=full_code.split('.')[0],
                    full_code=full_code,
                    name=names.get(full_code)
                )
                results[full_code] = result
        
        return results
    
    def get_alert_color(self, level: AlertLevel) -> str:
        """获取预警级别对应的颜色"""
        colors = {
            AlertLevel.NORMAL: "green",
            AlertLevel.WATCH: "yellow",
            AlertLevel.WARNING: "orange",
            AlertLevel.CRITICAL: "red"
        }
        return colors.get(level, "gray")
    
    def get_alert_emoji(self, level: AlertLevel) -> str:
        """获取预警级别对应的表情"""
        emojis = {
            AlertLevel.NORMAL: "🟢",
            AlertLevel.WATCH: "🟡",
            AlertLevel.WARNING: "🟠",
            AlertLevel.CRITICAL: "🔴"
        }
        return emojis.get(level, "⚪")


# 全局计算器实例（使用默认阈值）
diff_calculator = DiffCalculator()
