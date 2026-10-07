"""查询/筛选模块：在清洗结果上按日期或任意列条件筛选。"""

import re
from typing import List, Optional

import pandas as pd


def normalize_date(text: str) -> str:
    """把常见日期写法统一成 'YYYY-MM-DD'。

    支持 "2026.10.6"、"2026-10-6"、"2026/10/06"、"2026年10月6日" 等。
    """
    t = str(text).strip().replace("年", "-").replace("月", "-").replace("日", "")
    parts = re.split(r"[./\-]", t)
    if len(parts) != 3:
        raise ValueError(f"无法识别的日期：{text}")
    y, m, d = parts
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def filter_by_date(
    df: pd.DataFrame,
    column: str,
    *,
    date: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """按日期列筛选（date=某天，start/end=闭区间）。"""
    if column not in df.columns:
        raise ValueError(f"日期列不存在：{column}")

    # 统一转成日期时间再比较，兼容文本和日期两种类型
    col = pd.to_datetime(df[column], errors="coerce", format="mixed")
    mask = pd.Series(True, index=df.index)

    if date is not None:
        mask &= col == pd.to_datetime(normalize_date(date))
    if start is not None:
        mask &= col >= pd.to_datetime(normalize_date(start))
    if end is not None:
        mask &= col <= pd.to_datetime(normalize_date(end))

    return df[mask]


def filter_by_kv(df: pd.DataFrame, conditions: List[str]) -> pd.DataFrame:
    """按 'key=value' 精确匹配筛选，支持重复多个条件。"""
    for cond in conditions:
        if "=" not in cond:
            raise ValueError(f"筛选条件格式应为 key=value：{cond}")
        key, value = cond.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key not in df.columns:
            raise ValueError(f"筛选列不存在：{key}")
        df = df[df[key].astype(str).str.strip() == value]
    return df
