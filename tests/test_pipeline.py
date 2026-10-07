"""清洗流水线单元测试。"""

import numpy as np
import pandas as pd
import pytest

from cleaner import pipeline


def test_normalize_column_names():
    df = pd.DataFrame(columns=[" 姓名 ", " 年龄", "城市 "])
    pipeline.normalize_column_names(df)
    assert list(df.columns) == ["姓名", "年龄", "城市"]

    df2 = pd.DataFrame(columns=["Order Date"])
    pipeline.normalize_column_names(df2)
    assert list(df2.columns) == ["order_date"]


def test_trim_text_columns():
    df = pd.DataFrame({"a": [" x ", "y"], "b": [1, 2]})
    out = pipeline.trim_text_columns(df.copy())
    assert list(out["a"]) == ["x", "y"]
    assert list(out["b"]) == [1, 2]  # 数值列不受影响


def test_whitespace_to_nan():
    df = pd.DataFrame({"a": ["", "  ", "x", np.nan]})
    out = pipeline.whitespace_to_nan(df.copy())
    assert out["a"].isna().sum() == 3
    assert out.loc[2, "a"] == "x"


def test_convert_types():
    df = pd.DataFrame({"num": ["1.5", "2", "3.0"], "txt": ["a", "b", "c"]})
    out = pipeline.convert_types(df.copy())
    assert pd.api.types.is_float_dtype(out["num"])
    assert out["txt"].dtype == object


def test_clean_dedupe_and_dropna():
    df = pd.DataFrame({
        "名称": [" 张三 ", "张三", "李四", ""],
        "数量": ["1", "1", "2", "3"],
    })
    rules = {
        "normalize_column_names": False,
        "trim_whitespace": True,
        "drop_duplicates": True,
        "convert_types": True,
        "missing": {"strategy": "drop"},
        "standardize_dates": {"enabled": False},
    }
    out = pipeline.clean(df.copy(), rules)
    assert len(out) == 2
    assert set(out["名称"]) == {"张三", "李四"}


def test_clean_date_standardize():
    df = pd.DataFrame({"下单时间": ["2024/03/01", "2024-03-02"]})
    rules = {
        "trim_whitespace": False,
        "drop_duplicates": False,
        "normalize_column_names": False,
        "convert_types": False,
        "missing": {"strategy": "drop"},
        "standardize_dates": {"enabled": True, "columns": ["下单时间"]},
    }
    out = pipeline.clean(df.copy(), rules)
    assert pd.api.types.is_datetime64_any_dtype(out["下单时间"])


def test_read_source_csv(tmp_path):
    p = tmp_path / "d.csv"
    pd.DataFrame({"a": [1, 2]}).to_csv(p, index=False)
    df = pipeline.read_source(str(p))
    assert list(df.columns) == ["a"]
    assert len(df) == 2


def test_read_source_unsupported():
    with pytest.raises(ValueError):
        pipeline.read_source("data.json")


def test_write_output(tmp_path):
    df = pd.DataFrame({"a": [1, 2]})
    out = pipeline.write_output(df, str(tmp_path), "csv", "out.csv")
    assert (tmp_path / "out.csv").exists()
    read = pd.read_csv(out)
    assert list(read["a"]) == [1, 2]
