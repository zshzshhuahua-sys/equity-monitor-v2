"""
字段提取器
从PDF文本中提取股权激励关键字段
"""
import re
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ExtractedFields:
    """提取的字段结果"""
    option_ratio: Optional[float] = None      # 期权占比%
    exercise_price: Optional[float] = None   # 行权价
    incentive_object_count: Optional[int] = None  # 激励对象人数
    option_allocation: str = ""
    performance_requirements: str = ""


class FieldExtractor:
    """字段提取器"""

    # 正则表达式模式
    PATTERNS = {
        "option_ratio": [
            r"股本总额.*?(\d+\.?\d*)\s*%",
            r"占总股本[的]*比例[是为]*\s*([\d.]+)\s*%",
            r"期权数量[\s\S]*?占总股本[\s\S]*?([\d.]+)\s*%",
            r"授予总量[\s\S]*?占总股本[\s\S]*?([\d.]+)\s*%",
            r"占公司总股本[\s\S]*?([\d.]+)\s*%",
            r"占公司股本总额[\s\S]*?([\d.]+)\s*%",
        ],
        "exercise_price": [
            r"行权价格[是为:]+\s*每份\s*([\d.]+)\s*元",
            r"行权价格[是为:]+\s*每股\s*([\d.]+)\s*元",
            r"行权价格[是为:]+\s*([\d.]+)\s*元[/]?股",
            r"行权价[格]*[是为:]+\s*([\d.]+)",
            r"授予价格[是为:]+\s*([\d.]+)\s*元",
            r"以每份\s*([\d.]+)\s*元的价",
            r"以每股\s*([\d.]+)\s*元的价",
            # 支持含括号等修饰字符的格式，如"行权价格（含预留授予部分）为 36.22 元/份"
            r"行权价格[^\d]*?([\d.]+)\s*元",
            r"授予价格[^\d]*?([\d.]+)\s*元",
        ],
        "incentive_object_count": [
            r"激励对象总人数为(\d+)人",
            r"激励对象[\s\S]*?(\d+)\s*人",
            r"拟授予[\s\S]*?合计[\s\S]*?(\d+)\s*人",
            r"总人数[\s\S]*?(\d+)\s*人",
        ],
        "option_allocation": [
            r"股票期权分配情况[\s\S]*?(?=第五节|公司|$)",
            r"期权分配情况[\s\S]*?(?=第五节|公司|$)",
            r"激励对象间的分配情况如下[\s\S]*?(?=公司|$)",
        ],
        "performance_requirements": [
            r"公司层面业绩考核[\s\S]*?(?=个人|$)",
            r"业绩考核要求[\s\S]*?(?=二、|$)",
            r"考核年度[\s\S]*?净利润[\s\S]*?(?=公司|个人|$)",
        ],
    }

    @classmethod
    def extract_number(cls, text: str, field: str) -> Optional[float]:
        if not text or field not in cls.PATTERNS:
            return None

        for pattern in cls.PATTERNS[field]:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if not matches:
                continue
            try:
                value = float(matches[0])
                if field == "option_ratio" and 0 < value < 100:
                    return value
                if field in ("exercise_price",) and value > 0:
                    return value
                if field == "incentive_object_count" and value > 0:
                    return int(value)
            except (ValueError, TypeError):
                continue
        return None

    @classmethod
    def extract_text(cls, text: str, field: str, max_length: int = 500) -> str:
        if not text or field not in cls.PATTERNS:
            return ""

        for pattern in cls.PATTERNS[field]:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                result = matches[0] if isinstance(matches[0], str) else str(matches[0])
                return result.strip().replace("\n", " ")[:max_length]
        return ""


def extract_fields_from_text(text: str) -> ExtractedFields:
    """从PDF文本中提取所有关键字段"""
    if not text:
        return ExtractedFields()

    fields = ExtractedFields()
    fields.option_ratio = FieldExtractor.extract_number(text, "option_ratio")
    fields.exercise_price = FieldExtractor.extract_number(text, "exercise_price")
    fields.incentive_object_count = FieldExtractor.extract_number(text, "incentive_object_count")
    fields.option_allocation = FieldExtractor.extract_text(text, "option_allocation", max_length=500)
    fields.performance_requirements = FieldExtractor.extract_text(
        text, "performance_requirements", max_length=800
    )
    return fields
