"""数据清洗流水线：把各个清洗步骤串联起来，方便后续扩展。"""

import os

import numpy as np
import pandas as pd


def read_source(path: str) -> pd.DataFrame:
    """根据文件扩展名读取原始数据（支持 CSV 与 Excel）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return pd.read_csv(path)
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path)
    raise ValueError(f"暂不支持的输入格式：{ext}")


def normalize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """列名标准化：去首尾空格、转小写、空格转下划线。"""
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df


def trim_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    """对文本列去掉首尾空格。"""
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].map(lambda x: x.strip() if isinstance(x, str) else x)
    return df


def whitespace_to_nan(df: pd.DataFrame) -> pd.DataFrame:
    """把纯空白字符串（含空字符串）统一转换为缺失值 NaN。"""
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].map(
                lambda x: np.nan if isinstance(x, str) and x.strip() == "" else x
            )
    return df


def convert_types(df: pd.DataFrame) -> pd.DataFrame:
    """尝试把数值型字符串转换为数值，转换不了的列保持原样。"""
    for col in df.columns:
        try:
            df[col] = pd.to_numeric(df[col])
        except (ValueError, TypeError):
            # 该列包含无法转为数值的内容（如文本），保持原样
            continue
    return df


def clean(df: pd.DataFrame, rules: dict) -> pd.DataFrame:
    """按配置执行清洗规则，返回清洗后的 DataFrame。"""
    df = df.copy()

    if rules.get("normalize_column_names", True):
        df = normalize_column_names(df)

    if rules.get("trim_whitespace", True):
        df = trim_text_columns(df)

    # 把空白字符串转成缺失值，这样后续的缺失值处理才能真正生效
    df = whitespace_to_nan(df)

    if rules.get("drop_duplicates", True):
        df = df.drop_duplicates()

    missing = rules.get("missing", {})
    strategy = missing.get("strategy", "drop")
    if strategy == "drop":
        df = df.dropna(how="any")
    elif strategy == "fill":
        df = df.fillna(missing.get("fill_value", ""))

    if rules.get("convert_types", True):
        df = convert_types(df)

    date_rules = rules.get("standardize_dates", {})
    if date_rules.get("enabled"):
        for col in date_rules.get("columns", []):
            target = str(col).strip().lower().replace(" ", "_")
            if target in df.columns:
                # format="mixed" 支持同一列里混用 2024/03/01 和 2024-03-02 两种格式
                df[target] = pd.to_datetime(df[target], errors="coerce", format="mixed")

    return df


def write_output(
    df: pd.DataFrame,
    output_dir: str,
    fmt: str = "csv",
    filename: str | None = None,
) -> str:
    """把清洗结果写入输出目录，返回输出文件路径。

    支持自定义文件名 filename；不传时默认 cleaned.<fmt>。
    """
    os.makedirs(output_dir, exist_ok=True)

    if filename:
        out_path = os.path.join(output_dir, filename)
        ext = os.path.splitext(filename)[1].lower()
        if ext in (".csv", ".xlsx"):
            fmt = ext[1:]
    else:
        out_path = os.path.join(output_dir, f"cleaned.{fmt}")

    if fmt == "csv":
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
    elif fmt == "xlsx":
        df.to_excel(out_path, index=False)
    else:
        raise ValueError(f"暂不支持的输出格式：{fmt}")
    return out_path
