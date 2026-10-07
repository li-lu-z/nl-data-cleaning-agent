"""Function Calling 工具定义与安全执行。

大模型通过调用这些工具来操作 DataFrame，后端按结构化参数执行，
避免让模型直接生成任意 Python 代码，更安全、更可控。
"""

import pandas as pd

from cleaner import pipeline, query


def _resolve_column(df: pd.DataFrame, name: str):
    """列名模糊匹配。"""
    name = str(name).strip()
    if name in df.columns:
        return name
    for col in df.columns:
        if col.startswith(name) or name.startswith(col) or name in col or col in name:
            return col
    return None


def _tool(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _str_prop(desc, enum=None):
    p = {"type": "string", "description": desc}
    if enum:
        p["enum"] = enum
    return p


TOOLS = [
    _tool(
        "filter_by_column",
        "按某个文本列筛选：等于某个值，或包含某个关键词",
        {
            "column": _str_prop("列名"),
            "value": _str_prop("要匹配的值或关键词"),
            "mode": _str_prop("匹配方式", ["equals", "contains"]),
        },
        ["column", "value"],
    ),
    _tool(
        "filter_by_number",
        "按数值列比较筛选，例如 重量大于10",
        {
            "column": _str_prop("列名"),
            "operator": _str_prop("比较运算符", [">", ">=", "<", "<="]),
            "value": {"type": "number", "description": "比较的数值"},
        },
        ["column", "operator", "value"],
    ),
    _tool(
        "filter_by_date",
        "按日期列筛选：单日、起止区间，或某天之前/之后",
        {
            "column": _str_prop("日期列名"),
            "date": _str_prop("单日，如 2026-10-06，可选"),
            "start": _str_prop("开始日期（含），可选"),
            "end": _str_prop("结束日期（含），可选"),
        },
        ["column"],
    ),
    _tool(
        "sort_rows",
        "按某一列排序",
        {
            "column": _str_prop("列名"),
            "ascending": {"type": "boolean", "description": "是否升序，默认 true"},
        },
        ["column"],
    ),
    _tool(
        "drop_duplicates",
        "删除重复行，可按指定列去重",
        {
            "subset": {
                "type": "array",
                "items": {"type": "string"},
                "description": "去重依据的列名，空数组表示整行完全重复",
            }
        },
        [],
    ),
    _tool("drop_missing", "删除含空值/空白的行", {}, []),
    _tool(
        "fill_missing",
        "把空值填充为指定值",
        {"value": _str_prop("填充值，如 0 或 未知")},
        ["value"],
    ),
    _tool("convert_to_numeric", "把数值型字符串列转换为数值类型", {}, []),
    _tool(
        "standardize_dates",
        "把日期列统一成 YYYY-MM-DD 格式",
        {
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "日期列名，空数组表示自动选择所有日期/时间列",
            }
        },
        [],
    ),
    _tool(
        "group_aggregate",
        "分组统计：计数、求和、平均、最大、最小",
        {
            "group_column": _str_prop("分组列名"),
            "aggregation": _str_prop("聚合方式", ["count", "sum", "mean", "max", "min"]),
            "value_column": _str_prop("数值列名（count 时可为空）"),
        },
        ["group_column", "aggregation"],
    ),
    _tool(
        "select_columns",
        "只保留指定的列",
        {
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要保留的列名",
            }
        },
        ["columns"],
    ),
    _tool(
        "drop_columns",
        "删除指定的列",
        {
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要删除的列名",
            }
        },
        ["columns"],
    ),
]


def _resolve_list(df, columns):
    out = []
    for c in columns or []:
        r = _resolve_column(df, c)
        if r is None:
            raise ValueError(f"列不存在：{c}")
        if r not in out:
            out.append(r)
    return out


def execute_tool(name: str, args: dict, df: pd.DataFrame):
    """执行一个工具，返回 (新DataFrame, 描述, 脚本代码)。"""
    if name == "filter_by_column":
        col = _resolve_column(df, args.get("column", ""))
        if col is None:
            raise ValueError(f"列不存在：{args.get('column')}")
        value = str(args.get("value", ""))
        mode = args.get("mode", "equals")
        if mode == "contains":
            out = df[df[col].astype(str).str.contains(value, na=False)]
            code = f"df = df[df[{col!r}].astype(str).str.contains({value!r}, na=False)]"
            desc = f"已按“{col}”包含“{value}”筛选，命中 {len(out)} 行。"
        else:
            out = df[df[col].astype(str).str.strip() == value]
            code = f"df = df[df[{col!r}].astype(str).str.strip() == {value!r}]"
            desc = f"已按“{col} = {value}”筛选，命中 {len(out)} 行。"
        return out, desc, code

    if name == "filter_by_number":
        col = _resolve_column(df, args.get("column", ""))
        if col is None:
            raise ValueError(f"列不存在：{args.get('column')}")
        op = args.get("operator", ">")
        value = float(args.get("value", 0))
        series = pd.to_numeric(df[col], errors="coerce")
        op_map = {">": series > value, ">=": series >= value, "<": series < value, "<=": series <= value}
        out = df[op_map[op]]
        code = f'df = df[pd.to_numeric(df[{col!r}], errors="coerce") {op} {value}]'
        return out, f"已按“{col} {op} {value}”筛选，命中 {len(out)} 行。", code

    if name == "filter_by_date":
        col = _resolve_column(df, args.get("column", ""))
        if col is None:
            raise ValueError(f"列不存在：{args.get('column')}")
        date = args.get("date")
        start = args.get("start")
        end = args.get("end")
        out = query.filter_by_date(
            df, col,
            date=query.normalize_date(date) if date else None,
            start=query.normalize_date(start) if start else None,
            end=query.normalize_date(end) if end else None,
        )
        cond = date or (f"{start} 到 {end}" if start and end else (f"自 {start}" if start else f"至 {end}"))
        code = f"df = df[df[{col!r}].astype(str).isin({cond!r})]" if date else f"# 日期筛选：{col} {cond}"
        return out, f"已按“{col}”筛选日期 {cond}，命中 {len(out)} 行。", code

    if name == "sort_rows":
        col = _resolve_column(df, args.get("column", ""))
        if col is None:
            raise ValueError(f"列不存在：{args.get('column')}")
        ascending = bool(args.get("ascending", True))
        out = df.sort_values(col, ascending=ascending)
        code = f"df = df.sort_values({col!r}, ascending={ascending})"
        order = "升序" if ascending else "降序"
        return out, f"已按“{col}”{order}排序。", code

    if name == "drop_duplicates":
        subset = _resolve_list(df, args.get("subset", [])) or None
        out = df.drop_duplicates(subset=subset)
        code = f"df = df.drop_duplicates(subset={subset!r})" if subset else "df = df.drop_duplicates()"
        return out, f"已去重，删除 {len(df) - len(out)} 行。", code

    if name == "drop_missing":
        tmp = pipeline.whitespace_to_nan(df)
        out = tmp.dropna(how="any")
        code = (
            "for _c in df.columns:\n"
            "    if df[_c].dtype == object:\n"
            "        df[_c] = df[_c].map(lambda x: None if isinstance(x, str) and x.strip() == '' else x)\n"
            "df = df.dropna(how='any')"
        )
        return out, f"已删除含空值的行，删除 {len(df) - len(out)} 行。", code

    if name == "fill_missing":
        value = str(args.get("value", ""))
        num = None
        try:
            num = float(value)
        except ValueError:
            num = None
        out = df.copy()
        for c in out.columns:
            if num is not None and pd.api.types.is_numeric_dtype(out[c]):
                out[c] = out[c].fillna(num)
            else:
                out[c] = out[c].fillna(value)
        code = (
            "for _c in df.columns:\n"
            "    if pd.api.types.is_numeric_dtype(df[_c]):\n"
            f"        df[_c] = df[_c].fillna({num})\n"
            "    else:\n"
            f"        df[_c] = df[_c].fillna({value!r})"
        ) if num is not None else f"df = df.fillna({value!r})"
        return out, f"已把空值填充为“{value}”。", code

    if name == "convert_to_numeric":
        out = pipeline.convert_types(df)
        code = (
            "for _c in df.columns:\n"
            "    try:\n"
            "        df[_c] = pd.to_numeric(df[_c])\n"
            "    except (ValueError, TypeError):\n"
            "        pass"
        )
        return out, "已把数值型字符串列转换为数值类型。", code

    if name == "standardize_dates":
        cols = _resolve_list(df, args.get("columns", []))
        if not cols:
            cols = [c for c in df.columns if ("日期" in c or "时间" in c)]
        out = df.copy()
        for c in cols:
            out[c] = pd.to_datetime(out[c], errors="coerce", format="mixed")
        code = f"for _c in {cols!r}:\n    df[_c] = pd.to_datetime(df[_c], errors='coerce', format='mixed')"
        return out, f"已统一日期格式，涉及列：{'、'.join(cols)}。", code

    if name == "group_aggregate":
        group_col = _resolve_column(df, args.get("group_column", ""))
        if group_col is None:
            raise ValueError(f"分组列不存在：{args.get('group_column')}")
        agg = args.get("aggregation", "count")
        if agg == "count":
            out = df.groupby(group_col).size().reset_index(name="数量")
            code = f"df = df.groupby({group_col!r}).size().reset_index(name='数量')"
        else:
            value_col = _resolve_column(df, args.get("value_column", ""))
            if value_col is None:
                raise ValueError("请指定数值列 value_column")
            tmp = df.copy()
            tmp[value_col] = pd.to_numeric(tmp[value_col], errors="coerce")
            out = tmp.groupby(group_col)[value_col].agg(agg).reset_index()
            out.columns = [group_col, agg]
            code = (
                f"df[{value_col!r}] = pd.to_numeric(df[{value_col!r}], errors='coerce')\n"
                f"df = df.groupby({group_col!r})[{value_col!r}].{agg}().reset_index()"
            )
        return out, f"已按“{group_col}”分组统计（{agg}），共 {len(out)} 组。", code

    if name == "select_columns":
        cols = _resolve_list(df, args.get("columns", []))
        out = df[cols]
        return out, f"已只保留列：{'、'.join(cols)}。", f"df = df[{cols!r}]"

    if name == "drop_columns":
        cols = _resolve_list(df, args.get("columns", []))
        out = df.drop(columns=cols)
        return out, f"已删除列：{'、'.join(cols)}。", f"df = df.drop(columns={cols!r})"

    raise ValueError(f"未知工具：{name}")
