"""
公告筛选规则引擎
统一管理选股/选公告的过滤逻辑

规则：
  排除：9开头（B股）、京股（430/83x/87x/88x）
  入库：标题含「股权激励草案」或「股票期权激励草案」
  排除：限制性股票、修订稿、意见书等
  同一股同日多版：只保留 announcement_time 最新的一条
"""
from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass, field
from typing import Optional, List


# ============================
# 配置：排除词表
# ============================

# 标题中出现以下任意词 → 排除（草案摘要除外）
EXCLUDE_TITLE_KEYWORDS: List[str] = [
    "限制性股票",
    "修订稿",
    "修订说明",
    "法律意见书",
    "独立财务顾问报告",
    "核查意见",
    "专项核查",
    "保荐机构",
    "摘要",          # 草案摘要也排除，只留正文
    "实施考核管理办法",
    "激励对象名单",
    "授予公告",
    "调整公告",
    "注销公告",
    "终止实施",
    "补充公告",
    "问询函回复",
    "反馈意见",
    "落实情况",
    "延期公告",
    "二次修订稿",
]

# 标题中必须同时包含以下「激励类关键词」之一
# 标题必须精确包含以下短语之一（必须同时含"草案"）
REQUIRE_INCENTIVE_PHRASES: List[str] = [
    "股权激励草案",
    "股权激励计划",
    "股票期权激励草案",
    "股票期权激励计划",
]

# ============================
# 京股号段前缀
# ============================

BJ_EXCHANGE_PREFIXES: List[str] = [
    "430", "831", "832", "833", "834", "835", "836", "837", "838",
    "870", "871", "872", "873",
]


# ============================
# 数据结构
# ============================

@dataclass
class AnnouncementRaw:
    """原始公告数据"""
    announcement_id: str
    stock_code: str
    stock_name: str
    exchange: str = ""         # SH / SZ
    title: str = ""
    publish_date: str = ""     # YYYY-MM-DD
    announcement_time: Optional[int] = None  # 毫秒时间戳
    pdf_url: str = ""
    adjunct_url: str = ""       # cninfo 原始字段名


@dataclass
class FilterResult:
    """过滤结果"""
    is_eligible: bool
    exclude_reason: Optional[str] = None
    normalized_title: str = ""
    match_incentive_keyword: Optional[str] = None
    source_hash: str = ""


# ============================
# 规则引擎
# ============================

class AnnouncementRuleEngine:
    """公告筛选规则引擎"""

    @staticmethod
    def filter_stock(stock_code: str) -> tuple[bool, Optional[str]]:
        """
        过滤股票：排除B股和京股

        Args:
            stock_code: 6位股票代码

        Returns:
            (是否通过, 排除原因)
        """
        if not stock_code or len(stock_code) != 6:
            return False, "无效股票代码"

        # 排除B股（9开头）
        if stock_code.startswith("9"):
            return False, "B股（9开头）"

        # 排除京股
        prefix3 = stock_code[:3]
        if prefix3 in BJ_EXCHANGE_PREFIXES:
            return False, "京股"

        # 排除其他北交所相关（870/871/872/873等全匹配）
        if stock_code.startswith("87") or stock_code.startswith("88"):
            return False, "京股"

        return True, None

    @staticmethod
    def _normalize_title(title: str) -> str:
        """标题归一化：去除多余空格"""
        return re.sub(r"\s+", " ", title).strip()

    @classmethod
    def filter_title(cls, title: str) -> tuple[bool, Optional[str], Optional[str]]:
        """
        过滤公告标题：必须精确包含「股权激励草案」或「股票期权激励草案」短语，且不含排除词

        Args:
            title: 原始标题

        Returns:
            (是否通过, 命中/排除原因, 归一化标题)
        """
        normalized = cls._normalize_title(title)

        # 1. 检查必须精确包含激励草案短语
        matched_phrase = None
        for phrase in REQUIRE_INCENTIVE_PHRASES:
            if phrase in title:
                matched_phrase = phrase
                break
        if not matched_phrase:
            return False, "标题不含「股权激励草案」或「股票期权激励草案」短语", normalized

        # 2. 检查排除词
        for ex_kw in EXCLUDE_TITLE_KEYWORDS:
            if ex_kw in title:
                return False, f"标题含排除词「{ex_kw}」", normalized

        return True, None, normalized

    @classmethod
    def filter(cls, raw: AnnouncementRaw) -> FilterResult:
        """
        综合过滤：股票过滤 + 标题过滤

        Args:
            raw: 原始公告数据

        Returns:
            FilterResult
        """
        # 1. 股票过滤
        stock_ok, stock_reason = cls.filter_stock(raw.stock_code)
        if not stock_ok:
            return FilterResult(
                is_eligible=False,
                exclude_reason=stock_reason,
                normalized_title=cls._normalize_title(raw.title),
                source_hash=cls._compute_hash(raw),
            )

        # 2. 标题过滤
        title_ok, title_reason, normalized = cls.filter_title(raw.title)
        if not title_ok:
            return FilterResult(
                is_eligible=False,
                exclude_reason=title_reason,
                normalized_title=normalized,
                source_hash=cls._compute_hash(raw),
            )

        return FilterResult(
            is_eligible=True,
            exclude_reason=None,
            normalized_title=normalized,
            match_incentive_keyword=next(
                phrase for phrase in REQUIRE_INCENTIVE_PHRASES if phrase in raw.title
            ),
            source_hash=cls._compute_hash(raw),
        )

    @staticmethod
    def _compute_hash(raw: AnnouncementRaw) -> str:
        """
        计算去重hash：基于 announcement_id
        """
        key = f"{raw.announcement_id}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    @staticmethod
    def compute_latest_of_day_key(raw: AnnouncementRaw) -> tuple[str, str]:
        """
        返回「同日最新版」判断用的分组key
        Returns: (stock_code, publish_date)
        """
        return (raw.stock_code, raw.publish_date)


# ============================
# 便捷函数
# ============================

def filter_announcement(raw: AnnouncementRaw) -> FilterResult:
    """便捷函数：过滤单条公告"""
    return AnnouncementRuleEngine.filter(raw)


def filter_stock_code(stock_code: str) -> tuple[bool, Optional[str]]:
    """便捷函数：过滤股票代码"""
    return AnnouncementRuleEngine.filter_stock(stock_code)
