"""数据源抽象层：统一 CSV / Excel / JSON 的读取，兼容多种中文编码。

后续接入 API、数据库等新数据源时，只需在此新增分支即可。
"""

import io
from pathlib import Path

import pandas as pd


def read_dataframe(filename: str, raw: bytes | None = None) -> pd.DataFrame:
    """根据文件名/字节读取为 DataFrame。

    raw 为 None 时，把 filename 当作本地路径读取。
    """
    name = (filename or "").lower()
    if raw is None:
        raw = Path(filename).read_bytes()

    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(raw))

    if name.endswith(".json"):
        # 依次尝试常见编码，兼容中文
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                return pd.read_json(io.BytesIO(raw), encoding=enc)
            except (UnicodeDecodeError, ValueError):
                continue
        return pd.read_json(io.BytesIO(raw), encoding="utf-8-sig")

    if name.endswith((".csv", ".txt")):
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                return pd.read_csv(io.BytesIO(raw), encoding=enc)
            except (UnicodeDecodeError, pd.errors.ParserError):
                continue
        return pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig")

    raise ValueError(f"暂不支持的文件格式：{name}")
