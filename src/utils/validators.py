"""
数据验证器
验证导入的A股数据有效性
"""
import re
from typing import Tuple, List, Dict, Optional
from dataclasses import dataclass


def detect_exchange(symbol: str) -> str:
    """
    根据股票代码自动识别交易所

    规则：
    - 600-609, 688-689: 上海(SH)
    - 000-009, 300-309: 深圳(SZ)
    - 430, 83x, 87x, 88x: 北京(BJ)

    Args:
        symbol: 6位股票代码

    Returns:
        交易所代码 ('SH', 'SZ', 'BJ')

    Raises:
        ValueError: 无效的股票代码格式
    """
    if not symbol or len(symbol) != 6 or not symbol.isdigit():
        raise ValueError(f"Invalid symbol format: {symbol}, expected 6 digits")

    prefix = symbol[:3]

    if prefix in ('600', '601', '603', '605', '688', '689'):
        return 'SH'
    elif prefix in ('000', '001', '002', '003', '300', '301'):
        return 'SZ'
    elif prefix in ('430', '831', '832', '833', '834', '835', '836', '837', '838', '870', '871', '872', '873'):
        return 'BJ'
    else:
        # 默认推断
        if symbol.startswith('6'):
            return 'SH'
        elif symbol.startswith(('0', '3')):
            return 'SZ'
        else:
            raise ValueError(f"Cannot detect exchange for symbol: {symbol}")


@dataclass
class ValidationError:
    """验证错误"""
    row: int
    field: str
    value: str
    message: str


class StockValidator:
    """股票数据验证器"""
    
    @staticmethod
    def validate_symbol(symbol: str) -> Tuple[bool, Optional[str]]:
        """
        验证股票代码格式并识别交易所
        
        Returns:
            (是否有效, 交易所或错误信息)
        """
        if not symbol:
            return False, "股票代码不能为空"
        
        symbol = str(symbol).strip()
        
        # 检查是否为6位数字
        if not re.match(r'^\d{6}$', symbol):
            return False, f"股票代码格式错误: {symbol}，应为6位数字"
        
        # 识别交易所
        prefix = symbol[:3]
        
        if prefix in ['600', '601', '603', '605', '688', '689']:
            return True, 'SH'
        elif prefix in ['000', '001', '002', '003', '300', '301']:
            return True, 'SZ'
        elif prefix in ['430', '831', '832', '833', '834', '835', '836', '837', '838', '870', '871', '872', '873']:
            return True, 'BJ'
        else:
            # 默认规则
            if symbol.startswith('6'):
                return True, 'SH'
            elif symbol.startswith('0') or symbol.startswith('3'):
                return True, 'SZ'
            else:
                return False, f"无法识别交易所: {symbol}"
    
    @staticmethod
    def validate_strike_price(price) -> Tuple[bool, Optional[str]]:
        """验证执行价格"""
        try:
            price = float(price)
            if price <= 0:
                return False, f"执行价格必须大于0: {price}"
            return True, None
        except (ValueError, TypeError):
            return False, f"执行价格格式错误: {price}"
    
    @staticmethod
    def validate_quantity(quantity) -> Tuple[bool, Optional[str]]:
        """验证数量（可选）"""
        if quantity is None or quantity == '':
            return True, None
        
        try:
            qty = int(quantity)
            if qty < 0:
                return False, f"数量不能为负数: {qty}"
            return True, None
        except (ValueError, TypeError):
            return False, f"数量格式错误: {quantity}"
    
    @staticmethod
    def validate_threshold(threshold) -> Tuple[bool, Optional[str]]:
        """验证自定义阈值（可选）"""
        if threshold is None or threshold == '':
            return True, None
        
        try:
            t = float(threshold)
            if t < 0 or t > 1:
                return False, f"阈值应在0-1之间: {t}"
            return True, None
        except (ValueError, TypeError):
            return False, f"阈值格式错误: {threshold}"
    
    def validate_row(self, row: Dict, row_number: int) -> List[ValidationError]:
        """验证单行数据"""
        errors = []
        
        # 验证股票代码
        symbol = row.get('symbol', row.get('股票代码', row.get('code', '')))
        valid, result = self.validate_symbol(symbol)
        if not valid:
            errors.append(ValidationError(
                row=row_number,
                field='symbol',
                value=str(symbol),
                message=result
            ))
        
        # 验证执行价格
        price = row.get('strike_price', row.get('执行价格', row.get('price', 0)))
        valid, error = self.validate_strike_price(price)
        if not valid:
            errors.append(ValidationError(
                row=row_number,
                field='strike_price',
                value=str(price),
                message=error
            ))
        
        # 验证数量（可选）
        quantity = row.get('quantity', row.get('数量', row.get('qty', None)))
        valid, error = self.validate_quantity(quantity)
        if not valid:
            errors.append(ValidationError(
                row=row_number,
                field='quantity',
                value=str(quantity),
                message=error
            ))
        
        # 验证阈值（可选）
        threshold = row.get('custom_threshold', row.get('阈值', None))
        valid, error = self.validate_threshold(threshold)
        if not valid:
            errors.append(ValidationError(
                row=row_number,
                field='custom_threshold',
                value=str(threshold),
                message=error
            ))
        
        return errors


# 全局验证器实例
validator = StockValidator()
