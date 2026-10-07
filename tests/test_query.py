"""查询/筛选单元测试。"""

import pandas as pd
import pytest

from cleaner import query


def test_normalize_date():
    assert query.normalize_date("2026.10.6") == "2026-10-06"
    assert query.normalize_date("2026-1-6") == "2026-01-06"
    assert query.normalize_date("2026/10/06") == "2026-10-06"
    assert query.normalize_date("2026年10月6日") == "2026-10-06"


def test_filter_by_date_single():
    df = pd.DataFrame({"日期": ["2024-01-01", "2024-02-01", "2024-03-01"]})
    out = query.filter_by_date(df, "日期", date="2024.2.1")
    assert len(out) == 1


def test_filter_by_date_range():
    df = pd.DataFrame({"日期": ["2024-01-01", "2024-02-01", "2024-03-01"]})
    out = query.filter_by_date(df, "日期", start="2024.1.1", end="2024.2.28")
    assert len(out) == 2


def test_filter_by_date_missing_column():
    df = pd.DataFrame({"日期": ["2024-01-01"]})
    with pytest.raises(ValueError):
        query.filter_by_date(df, "不存在的列", date="2024.1.1")


def test_filter_by_kv():
    df = pd.DataFrame({
        "城市": ["上海", "北京", "上海"],
        "状态": ["已签收", "运输中", "已签收"],
    })
    out = query.filter_by_kv(df, ["城市=上海", "状态=已签收"])
    assert len(out) == 2


def test_filter_by_kv_missing_column():
    df = pd.DataFrame({"城市": ["上海"]})
    with pytest.raises(ValueError):
        query.filter_by_kv(df, ["不存在=值"])
