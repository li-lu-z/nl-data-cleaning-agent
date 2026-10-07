"""网页后端与 Function Calling 工具单元测试。"""

import pandas as pd

import app
import tools


def test_py_op():
    assert app.py_op("大于") == ">"
    assert app.py_op("不超过") == "<="
    assert app.py_op(">=") == ">="


def test_build_script_contains_preprocess_and_steps():
    session = {
        "filename": "x.csv",
        "steps": [{"desc": "去重", "code": "df = df.drop_duplicates()"}],
    }
    script = app.build_script(session)
    assert "基础预处理" in script
    assert "df = df.drop_duplicates()" in script
    compile(script, "clean_script.py", "exec")


def test_tool_filter_by_number():
    df = pd.DataFrame({"重量(kg)": [1.2, 25.0, 3.6], "城市": ["上海", "北京", "上海"]})
    out, _desc, _code = tools.execute_tool(
        "filter_by_number", {"column": "重量", "operator": ">", "value": 10}, df
    )
    assert len(out) == 1
    assert out.iloc[0]["城市"] == "北京"


def test_tool_group_aggregate():
    df = pd.DataFrame({"城市": ["上海", "北京", "上海"], "运费(元)": [10, 20, 15]})
    out, _desc, _code = tools.execute_tool(
        "group_aggregate",
        {"group_column": "城市", "aggregation": "sum", "value_column": "运费"},
        df,
    )
    assert len(out) == 2
    assert set(out["城市"]) == {"上海", "北京"}
