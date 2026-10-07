"""数据清洗聊天网页的后端。

功能：
    - 上传 CSV / Excel 文件
    - 用自然语言（规则匹配）清洗、查询、排序数据
    - 下载当前结果

启动：
    python app.py
然后浏览器打开 http://127.0.0.1:5000
"""

import io
import json
import os
import re
import uuid
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, request, send_file, send_from_directory

from cleaner import pipeline, query

import datasource
import store
import tools


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

# 内存缓存，磁盘落库见 store.py（SQLite 持久化）
STORAGE: dict[str, dict] = {}

# 常见日期写法，例如 2026.10.6 / 2026-10-6 / 2026/10/6 / 2026年10月6日
DATE_PATTERN = re.compile(r"\d{4}[年./\-]\d{1,2}[月./\-]\d{1,2}日?")

store.init_db()


def get_session(session_id: str):
    """先从内存缓存取，未命中再从 SQLite 加载。"""
    if session_id in STORAGE:
        return STORAGE[session_id]
    loaded = store.load_session(session_id)
    if loaded is not None:
        STORAGE[session_id] = loaded
        return loaded
    return None


def persist_session(session_id: str) -> None:
    """把内存中的会话写回 SQLite。"""
    s = STORAGE.get(session_id)
    if s is None:
        return
    store.save_session(
        session_id,
        s.get("filename", ""),
        s["df"],
        s["original"],
        s.get("history", []),
        s.get("steps", []),
    )


def read_upload(file_storage) -> pd.DataFrame:
    """根据上传文件读取 DataFrame（委托给数据源抽象层）。"""
    return datasource.read_dataframe(file_storage.filename, file_storage.read())


def make_preview(df: pd.DataFrame, n: int = 10) -> dict:
    """把 DataFrame 前 n 行转成前端可用的 JSON。"""
    d = df.head(n)
    records = json.loads(d.to_json(orient="records", date_format="iso", force_ascii=False))
    return {"columns": list(d.columns), "rows": records}


def state_of(session: dict) -> dict:
    """返回当前会话状态（行数、列名、预览）。"""
    df = session["df"]
    return {
        "rows": len(df),
        "columns": list(df.columns),
        "preview": make_preview(df),
    }


def find_date_column(df: pd.DataFrame):
    """自动找一个日期列：优先列名含“日期/时间”，其次 datetime 类型。"""
    for col in df.columns:
        if "日期" in col or "时间" in col:
            return col
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            return col
    return None


def resolve_column(df: pd.DataFrame, name: str):
    """按用户输入的列名模糊匹配，返回真实列名；找不到返回 None。"""
    name = name.strip()
    if name in df.columns:
        return name
    # 前缀或反向前缀匹配，例如“重量”->“重量(kg)”
    for col in df.columns:
        if col.startswith(name) or name.startswith(col):
            return col
    # 子串包含
    for col in df.columns:
        if name in col or col in name:
            return col
    return None


def describe(df: pd.DataFrame) -> str:
    """生成一段数据概况文本。"""
    cols = "、".join(str(c) for c in df.columns)
    return f"当前共 {len(df)} 行、{len(df.columns)} 列。列名：{cols}。"


def parse_number(text: str):
    """把字符串解析成数值，失败返回 None。"""
    try:
        return float(str(text).replace(",", "").replace("，", "").strip())
    except (ValueError, TypeError):
        return None


def agg_label(agg: str) -> str:
    """把聚合方式翻译成中文列名。"""
    return {
        "count": "数量",
        "mean": "平均值",
        "sum": "总和",
        "max": "最大值",
        "min": "最小值",
    }.get(agg, agg)


def find_value_column(df: pd.DataFrame, group_col: str, msg: str):
    """在分组统计时，找用于求平均/求和等的数值列。"""
    for c in df.columns:
        if c != group_col and c in msg:
            return c
    # 用去掉括号后的“简称”匹配，例如“重量(kg)”->“重量”
    for c in df.columns:
        if c == group_col:
            continue
        stem = re.sub(r"[（）()].*$", "", c)
        if stem and stem in msg:
            return c
    # 兜底：第一个能转成数值的列
    for c in df.columns:
        if c != group_col and not pd.to_numeric(df[c], errors="coerce").isna().all():
            return c
    return None


HELP_TEXT = (
    "我能听懂这些指令（可混用）：\n"
    "1. 查日期：找出2026.10.6的数据 / 2026年10月1日到10月31日\n"
    "2. 按列筛选：目的城市=上海 / 状态是已签收\n"
    "3. 数值筛选：重量大于10 / 运费<=20 / 重量在5到10之间\n"
    "4. 去重：删除重复行 / 按运单号去重\n"
    "5. 去空格：去掉空格 / 清理空格\n"
    "6. 空值处理：删除空值 / 把空值填成0\n"
    "7. 转数值：把数字列转成数值\n"
    "8. 统一日期：把日期统一格式\n"
    "9. 排序：按重量从大到小排序 / 按日期升序\n"
    "10. 分组统计：每个城市有多少单 / 按城市统计平均重量 / 按城市统计运费总和\n"
    "11. 选列/删列：只看城市和重量 / 删除运单号列\n"
    "12. 查看：前10行 / 数据概况\n"
    "13. 重置：恢复全部数据\n"
    "14. 下载：导出结果"
)


def handle_message_rules(session: dict, message: str) -> dict:
    """解析自然语言指令，返回 {reply, changed, preview_n}。"""
    df = session["df"]
    msg = message.strip()

    # 1) 帮助
    if re.search(r"(帮助|help|你能做什么|怎么用|功能|指令)", msg):
        return {"reply": HELP_TEXT, "changed": False}

    # 2) 数据概况
    if re.search(r"(概况|概览|信息|多少行|多少列|几行|几列|列名|字段|看看数据|描述)", msg):
        return {"reply": describe(df), "changed": False}

    # 3) 重置
    if re.search(r"(重置|恢复|回到原始|撤销|显示全部|全部数据)", msg):
        session["df"] = session["original"].copy()
        session["steps"] = []
        return {"reply": f"已恢复为上传时的全部数据。{describe(session['df'])}", "changed": True}

    # 4) 去重（支持“按某列去重”）
    if re.search(r"(去重|删除重复|去掉重复|去除重复|重复行)", msg):
        m = re.search(r"按(?:照)?(.+?)(?:去重|删除重复|去掉重复|去除重复)", msg)
        subset = None
        if m:
            subset = resolve_column(df, m.group(1).strip())
            if subset is None:
                return {"reply": f"没找到列：{m.group(1).strip()}。现有列：{'、'.join(df.columns)}", "changed": False}
        before = len(df)
        session["df"] = df.drop_duplicates(subset=subset)
        scope = f"（按“{subset}”）" if subset else "（整行完全重复）"
        code = f"df = df.drop_duplicates(subset=[{subset!r}])" if subset else "df = df.drop_duplicates()"
        return {"reply": f"已去重{scope}：{before} 行 -> {len(session['df'])} 行，删除 {before - len(session['df'])} 行。", "changed": True, "code": code}

    # 5) 去空格
    if re.search(r"(去空格|清空格|去掉空格|去除空格|清理空格|首尾空格|trim)", msg):
        session["df"] = pipeline.trim_text_columns(df)
        code = (
            "for _c in df.columns:\n"
            "    if df[_c].dtype == object:\n"
            "        df[_c] = df[_c].map(lambda x: x.strip() if isinstance(x, str) else x)"
        )
        return {"reply": "已清理所有文本列的首尾空格。", "changed": True, "code": code}

    # 6) 删除空值 / 缺失
    if re.search(r"(删除空值|删除缺失|去掉空行|删除空行|删除有空的|dropna|缺失值)", msg):
        before = len(df)
        tmp = pipeline.whitespace_to_nan(df)
        session["df"] = tmp.dropna(how="any")
        code = (
            "for _c in df.columns:\n"
            "    if df[_c].dtype == object:\n"
            "        df[_c] = df[_c].map(lambda x: None if isinstance(x, str) and x.strip() == '' else x)\n"
            "df = df.dropna(how='any')"
        )
        return {"reply": f"已删除含空值的行（空白也算空）：{before} 行 -> {len(session['df'])} 行。", "changed": True, "code": code}

    # 6.5) 填充空值：把空值填成 0 / 缺失值填充为 未知
    m = re.search(r"(?:空值|缺失值|空白|空)\s*(?:填|填充|替换)\s*(?:成|为)?\s*(.+)$", msg)
    if m:
        fill_val = m.group(1).strip()
        num = parse_number(fill_val)
        for c in df.columns:
            if num is not None and pd.api.types.is_numeric_dtype(df[c]):
                df[c] = df[c].fillna(num)
            else:
                df[c] = df[c].fillna(fill_val)
        session["df"] = df
        if num is not None:
            code = (
                "for _c in df.columns:\n"
                "    if pd.api.types.is_numeric_dtype(df[_c]):\n"
                f"        df[_c] = df[_c].fillna({num})\n"
                "    else:\n"
                f"        df[_c] = df[_c].fillna({fill_val!r})"
            )
        else:
            code = f"df = df.fillna({fill_val!r})"
        return {"reply": f"已把空值填充为“{fill_val}”。", "changed": True, "code": code}

    # 7) 数值转换
    if re.search(r"(转.*数值|数值.*转|转成数字|转为数字|数值化|数字化)", msg):
        session["df"] = pipeline.convert_types(df)
        code = (
            "for _c in df.columns:\n"
            "    try:\n"
            "        df[_c] = pd.to_numeric(df[_c])\n"
            "    except (ValueError, TypeError):\n"
            "        pass"
        )
        return {"reply": "已尝试把数值型字符串列转换为数值类型。", "changed": True, "code": code}

    # 8) 日期统一
    if re.search(r"(日期统一|日期标准化|统一日期|日期格式|标准化日期)", msg):
        changed_cols = []
        for col in df.columns:
            if ("日期" in col or "时间" in col) and pd.api.types.is_object_dtype(df[col]):
                df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")
                changed_cols.append(col)
        if changed_cols:
            session["df"] = df
            code = f"for _c in {changed_cols!r}:\n    df[_c] = pd.to_datetime(df[_c], errors='coerce', format='mixed')"
            return {"reply": f"已统一日期格式为 YYYY-MM-DD，涉及列：{ '、'.join(changed_cols) }。", "changed": True, "code": code}
        return {"reply": "没找到需要统一的日期列。", "changed": False}

    # 9) 排序
    m = re.search(r"按(.{1,20}?)(从大到小|降序|从高到低|从小到大|升序|从低到高)?排序", msg)
    if m:
        typed = m.group(1).strip()
        direction = m.group(2) or ""
        ascending = direction not in ("从大到小", "降序", "从高到低")
        col = resolve_column(df, typed)
        if col is None:
            return {"reply": f"没找到列：{typed}。现有列：{'、'.join(df.columns)}", "changed": False}
        if col not in df.columns:
            return {"reply": f"没找到列：{col}。现有列：{'、'.join(df.columns)}", "changed": False}
        session["df"] = df.sort_values(col, ascending=ascending)
        order = "升序" if ascending else "降序"
        code = f"df = df.sort_values({col!r}, ascending={ascending})"
        return {"reply": f"已按“{col}”{order}排序。", "changed": True, "code": code}

    # 10) 日期筛选（单日 / 区间 / 前后）
    dates = DATE_PATTERN.findall(msg)
    if dates:
        col = find_date_column(df)
        if col is None:
            return {"reply": "没找到日期列，请先告诉我日期在哪一列（例如“日期列是 下单时间”）。", "changed": False}
        if len(dates) >= 2:
            start, end = sorted((query.normalize_date(d) for d in dates[:2]))
            session["df"] = query.filter_by_date(df, col, start=start, end=end)
            reply = f"已筛选“{col}”在 {start} 到 {end} 之间：命中 {len(session['df'])} 行。"
            code = (
                f"df = df[(pd.to_datetime(df[{col!r}], format='mixed') >= pd.to_datetime({start!r})) & "
                f"(pd.to_datetime(df[{col!r}], format='mixed') <= pd.to_datetime({end!r}))]"
            )
        elif ("之前" in msg) or ("以前" in msg):
            end = query.normalize_date(dates[0])
            session["df"] = query.filter_by_date(df, col, end=end)
            reply = f"已筛选“{col}”在 {end} 之前（含当天）：命中 {len(session['df'])} 行。"
            code = f"df = df[pd.to_datetime(df[{col!r}], format='mixed') <= pd.to_datetime({end!r})]"
        elif ("之后" in msg) or ("以后" in msg):
            start = query.normalize_date(dates[0])
            session["df"] = query.filter_by_date(df, col, start=start)
            reply = f"已筛选“{col}”在 {start} 之后（含当天）：命中 {len(session['df'])} 行。"
            code = f"df = df[pd.to_datetime(df[{col!r}], format='mixed') >= pd.to_datetime({start!r})]"
        else:
            day = query.normalize_date(dates[0])
            session["df"] = query.filter_by_date(df, col, date=day)
            reply = f"已筛选“{col}”等于 {day}：命中 {len(session['df'])} 行。"
            code = f"df = df[pd.to_datetime(df[{col!r}], format='mixed') == pd.to_datetime({day!r})]"
        return {"reply": reply, "changed": True, "code": code}

    # 10.5) 数值比较筛选：重量大于10 / 运费<=20 / 重量超过5
    m = re.search(
        r"(?P<col>[\w\u4e00-\u9fa5（）()]+?)\s*(?P<op>>=|<=|>|<|大于等于|小于等于|不大于|不小于|不高于|不低于|不超过|大于|小于|高于|低于|超过)\s*(?P<val>\d+(?:\.\d+)?)",
        msg,
    )
    if m:
        col = resolve_column(df, m.group("col").strip())
        if col is None:
            return {"reply": f"没找到列：{m.group('col')}。现有列：{'、'.join(df.columns)}", "changed": False}
        num = parse_number(m.group("val"))
        op = m.group("op")
        series = pd.to_numeric(df[col], errors="coerce")
        if series.isna().all():
            return {"reply": f"“{col}”不是数值列，无法比较大小。", "changed": False}
        op_map = {
            ">": lambda: series > num,
            "<": lambda: series < num,
            ">=": lambda: series >= num,
            "<=": lambda: series <= num,
            "大于": lambda: series > num,
            "小于": lambda: series < num,
            "大于等于": lambda: series >= num,
            "小于等于": lambda: series <= num,
            "不小于": lambda: series >= num,
            "不大于": lambda: series <= num,
            "不低于": lambda: series >= num,
            "不高于": lambda: series <= num,
            "不超过": lambda: series <= num,
            "超过": lambda: series > num,
            "高于": lambda: series > num,
            "低于": lambda: series < num,
        }
        before = len(df)
        session["df"] = df[op_map[op]()]
        code = f'df = df[pd.to_numeric(df[{col!r}], errors="coerce") {py_op(op)} {num}]'
        return {"reply": f"已筛选“{col} {op} {num}”：{before} 行 -> {len(session['df'])} 行。", "changed": True, "code": code}

    # 10.6) 数值区间筛选：重量在5到10之间 / 运费在10-20
    m = re.search(
        r"(?P<col>[\w\u4e00-\u9fa5（）()]+?)\s*(?:在|介于)?\s*(?P<lo>\d+(?:\.\d+)?)\s*(?:到|至|和|~|-|—|－)\s*(?P<hi>\d+(?:\.\d+)?)\s*(?:之间)?",
        msg,
    )
    if m:
        col = resolve_column(df, m.group("col").strip())
        if col is None:
            return {"reply": f"没找到列：{m.group('col')}。现有列：{'、'.join(df.columns)}", "changed": False}
        lo, hi = parse_number(m.group("lo")), parse_number(m.group("hi"))
        series = pd.to_numeric(df[col], errors="coerce")
        if series.isna().all():
            return {"reply": f"“{col}”不是数值列，无法按区间筛选。", "changed": False}
        before = len(df)
        session["df"] = df[(series >= lo) & (series <= hi)]
        code = (
            f"df = df[(pd.to_numeric(df[{col!r}], errors='coerce') >= {lo}) & "
            f"(pd.to_numeric(df[{col!r}], errors='coerce') <= {hi})]"
        )
        return {"reply": f"已筛选“{col}”在 {lo} 到 {hi} 之间：{before} 行 -> {len(session['df'])} 行。", "changed": True, "code": code}

    # 10.7) 分组统计：按城市分组 / 每个城市有多少单 / 各城市的平均重量
    group_col = None
    gm = re.search(r"按(?:照)?(.+?)(?:分组|统计|汇总)", msg)
    if gm:
        group_col = gm.group(1).strip()
    else:
        gm = re.search(r"(?:每个|各个)(.+?)(?:有|的)", msg)
        if gm:
            group_col = gm.group(1).strip()
        else:
            gm = re.search(r"各(.+?)的", msg)
            if gm:
                group_col = gm.group(1).strip()
    if group_col is not None:
        group_col = resolve_column(df, group_col)
        if group_col is None:
            return {"reply": f"没找到分组列。现有列：{'、'.join(df.columns)}", "changed": False}

        if re.search(r"平均|均值", msg):
            agg = "mean"
        elif re.search(r"总和|合计|总计", msg):
            agg = "sum"
        elif re.search(r"最大|最高", msg):
            agg = "max"
        elif re.search(r"最小|最低", msg):
            agg = "min"
        else:
            agg = "count"

        if agg == "count":
            result = df.groupby(group_col).size().reset_index(name=agg_label("count"))
            code = f"df = df.groupby({group_col!r}).size().reset_index(name='数量')"
        else:
            value_col = find_value_column(df, group_col, msg)
            if value_col is None:
                return {"reply": "请告诉我对哪个数值列做统计，例如：按城市统计运费总和。", "changed": False}
            tmp = df.copy()
            tmp[value_col] = pd.to_numeric(tmp[value_col], errors="coerce")
            result = tmp.groupby(group_col)[value_col].agg(agg).reset_index()
            result.columns = [group_col, agg_label(agg)]
            code = (
                f"df[{value_col!r}] = pd.to_numeric(df[{value_col!r}], errors='coerce')\n"
                f"df = df.groupby({group_col!r})[{value_col!r}].{agg}().reset_index()"
            )

        session["df"] = result
        return {"reply": f"已按“{group_col}”分组统计（{agg_label(agg)}），共 {len(result)} 组。可输入“恢复全部数据”回到原表。", "changed": True, "code": code}

    # 10.8) 选择列：只看城市和重量 / 保留运单号、状态
    m = re.search(r"(?:只看|只显示|仅显示|只保留|保留|选择|只要)(.+)$", msg)
    if m:
        selected = []
        for c in re.split(r"[、，,和及\s]+", m.group(1).strip()):
            r = resolve_column(df, c)
            if r is None:
                return {"reply": f"没找到列：{c}。现有列：{'、'.join(df.columns)}", "changed": False}
            if r not in selected:
                selected.append(r)
        session["df"] = df[selected]
        code = f"df = df[{selected!r}]"
        return {"reply": f"已只保留这些列：{'、'.join(selected)}。", "changed": True, "code": code}

    # 10.9) 删除列：删除运单号列 / 去掉备注字段
    m = re.search(r"(?:删除|去掉|移除|不要)(.+?)(?:列|字段)?$", msg)
    if m:
        to_drop = []
        for c in re.split(r"[、，,和及\s]+", m.group(1).strip()):
            r = resolve_column(df, c)
            if r is None:
                return {"reply": f"没找到列：{c}。现有列：{'、'.join(df.columns)}", "changed": False}
            if r not in to_drop:
                to_drop.append(r)
        session["df"] = df.drop(columns=to_drop)
        code = f"df = df.drop(columns={to_drop!r})"
        return {"reply": f"已删除列：{'、'.join(to_drop)}。", "changed": True, "code": code}

    # 11) 按列筛选：支持 “列=值”“列 是 值”“列 等于 值”
    m = re.search(r"([\w\u4e00-\u9fa5（）()]+)\s*[=＝]\s*([\w\u4e00-\u9fa5.（）()]+)", msg)
    if m:
        col, val = m.group(1).strip(), m.group(2).strip()
    else:
        m = re.search(r"([\w\u4e00-\u9fa5（）()]+)\s*(?:是|为|等于)\s*([\w\u4e00-\u9fa5.（）()]+)", msg)
        col, val = (m.group(1).strip(), m.group(2).strip()) if m else (None, None)

    if col is not None:
        resolved = resolve_column(df, col)
        if resolved is None:
            return {"reply": f"没找到列：{col}。现有列：{'、'.join(df.columns)}", "changed": False}
        col = resolved
        before = len(df)
        session["df"] = query.filter_by_kv(df, [f"{col}={val}"])
        code = f'df = df[df[{col!r}].astype(str).str.strip() == {val!r}]'
        return {"reply": f"已按“{col} = {val}”筛选：{before} 行 -> {len(session['df'])} 行。", "changed": True, "code": code}

    # 12) 查看前 N 行
    m = re.search(r"(?:前|查看|显示)?\s*(\d+)\s*行", msg)
    if m:
        n = int(m.group(1))
        return {"reply": f"这是前 {min(n, len(df))} 行（数据共 {len(df)} 行，未做筛选）。", "changed": False, "preview_n": n}

    # 12.5) 导出脚本
    if re.search(r"(导出脚本|生成脚本|清洗脚本|复用脚本)", msg):
        return {"reply": "请点击页面右上角的“导出脚本”按钮，下载可复用的 Python 清洗脚本。", "changed": False}

    # 13) 下载
    if re.search(r"(下载|导出|保存)", msg):
        return {"reply": "请点击页面下方的“下载结果”按钮保存当前数据。", "changed": False}

    # 兜底：没匹配到任何规则
    return {
        "reply": f"抱歉，我没听懂“{msg}”这句话。\n\n你可以换一种说法，比如：\n"
                 "· 找出2026.10.6的数据\n"
                 "· 重量大于10\n"
                 "· 每个城市有多少单\n"
                 "· 删除重复行\n\n"
                 "输入“帮助”可查看全部支持的指令。",
        "changed": False,
    }


def get_deepseek_key() -> str:
    """读取 DeepSeek API Key：优先环境变量，其次项目目录下的 deepseek_key.txt。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    key_file = BASE_DIR / "deepseek_key.txt"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    return ""


def build_schema(df: pd.DataFrame) -> str:
    """把列名、类型、示例值整理成给大模型的上下文。"""
    lines = []
    for c in df.columns:
        sample = df[c].dropna().head(3).tolist()
        lines.append(f"- 列名：{c}，类型：{df[c].dtype}，示例：{sample}")
    return "\n".join(lines)


def _system_prompt(schema: str) -> str:
    return (
        "你是一个数据分析助手，用户会用中文提出一个针对 pandas DataFrame 的需求。\n"
        "当前数据表信息如下：\n"
        f"{schema}\n\n"
        "你可以调用函数工具来操作数据（筛选、清洗、排序、分组统计、选择列、删除列等）；\n"
        "如果用户只是问一个数字或一句话（如“一共有多少行”），直接输出文本回答，不要调用工具。\n"
        "如果用户的话是在延续上一轮（如“再按XX排序”），请结合对话历史理解，并在当前数据上继续操作。\n"
        "列名必须与上面给出的完全一致。"
    )


def _post_chat(api_key: str, messages: list, tools: list | None = None) -> dict:
    """调用 DeepSeek Chat 接口，返回一条 message（含 content 或 tool_calls）。"""
    import requests

    payload = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    resp = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]


def run_agent(api_key: str, message: str, schema: str, history: list, df: pd.DataFrame):
    """Agent 循环：让大模型多轮调用工具，直到完成整条操作。"""
    messages = [{"role": "system", "content": _system_prompt(schema)}]
    for h in history[-10:]:
        messages.append({
            "role": h.get("role", "user"),
            "content": str(h.get("content", ""))[:300],
        })
    messages.append({"role": "user", "content": message})

    steps = []
    final_text = ""

    # 最多 3 轮，避免无限循环
    for _ in range(3):
        msg = _post_chat(api_key, messages, tools.TOOLS)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            final_text = str(msg.get("content") or "")
            break

        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tool_calls})
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            new_df, desc, code = tools.execute_tool(name, args, df)
            df = new_df
            steps.append({"desc": desc, "code": code})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": json.dumps({"ok": True, "desc": desc, "rows": len(df)}, ensure_ascii=False),
            })

    return df, steps, final_text


def py_op(op: str) -> str:
    """把中文比较词翻译成 Python 运算符。"""
    mapping = {
        ">": ">", "<": "<", ">=": ">=", "<=": "<=",
        "大于": ">", "小于": "<", "大于等于": ">=", "小于等于": "<=",
        "不小于": ">=", "不大于": "<=", "不低于": ">=", "不高于": "<=",
        "不超过": "<=", "超过": ">", "高于": ">", "低于": "<",
    }
    return mapping.get(op, op)


def build_script(session: dict) -> str:
    """根据会话里记录的清洗步骤，生成一个可复用的 Python 脚本。"""
    lines = [
        "# 由「数据清洗助手」自动生成的可复用脚本",
        f"# 原始文件：{session.get('filename', '')}",
        "import pandas as pd",
        "",
        "# 请把下面的文件路径换成你自己的数据文件（如文件是 GBK 编码，可改成 encoding='gbk'）",
        'df = pd.read_csv("你的数据.csv", encoding="utf-8-sig")',
        "",
        "# 基础预处理（与网页上传时一致：列名标准化、去空格、数值转数字）",
        "df.columns = [str(c).strip().lower().replace(' ', '_') for c in df.columns]",
        "for _c in df.columns:",
        "    if df[_c].dtype == object:",
        "        df[_c] = df[_c].map(lambda x: x.strip() if isinstance(x, str) else x)",
        "for _c in df.columns:",
        "    try:",
        "        df[_c] = pd.to_numeric(df[_c])",
        "    except (ValueError, TypeError):",
        "        pass",
        "_seen, _new = {}, []",
        "for _c in df.columns:",
        "    if _c in _seen:",
        "        _seen[_c] += 1",
        "        _new.append(f'{_c}_{_seen[_c]}')",
        "    else:",
        "        _seen[_c] = 0",
        "        _new.append(_c)",
        "df.columns = _new",
        "",
    ]
    for i, step in enumerate(session.get("steps", []), 1):
        desc = str(step.get("desc", "")).replace("\n", " ").strip()
        code = str(step.get("code", "")).strip()
        lines.append(f"# 步骤{i}：{desc}")
        if code:
            lines.extend(code.splitlines())
            lines.append("")
    lines.append('df.to_csv("cleaned.csv", index=False, encoding="utf-8-sig")')
    lines.append('print("完成，共", len(df), "行")')
    return "\n".join(lines) + "\n"


def handle_message_llm(session: dict, message: str, api_key: str) -> dict:
    """用 Agent 循环执行大模型理解到的操作。"""
    df = session["df"]
    new_df, steps, final_text = run_agent(api_key, message, build_schema(df), session.get("history", []), df)

    if steps:
        session["df"] = new_df
        reply = "；".join(s["desc"] for s in steps)
        code = "\n".join(s["code"] for s in steps)
        return {"reply": reply, "changed": True, "code": code}

    return {"reply": final_text or "（没有回答内容）", "changed": False}


def handle_message(session: dict, message: str) -> dict:
    """调度：控制类指令走规则，其余优先走大模型，失败再退回规则。"""
    msg = message.strip()
    # 重置 / 下载会改变会话状态或触发前端行为，必须走规则
    if re.search(r"(重置|恢复|回到原始|撤销|显示全部|全部数据|下载|导出|保存|脚本)", msg):
        return handle_message_rules(session, message)

    key = get_deepseek_key()
    if key:
        try:
            return handle_message_llm(session, message, key)
        except Exception as exc:
            print(f"[大模型失败，退回规则] {exc}", flush=True)

    return handle_message_rules(session, message)


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.post("/api/upload")
def upload():
    file = request.files.get("file")
    if file is None or file.filename == "":
        return jsonify({"error": "没有收到文件"}), 400

    try:
        df = read_upload(file)
    except Exception as exc:  # 读取失败时给前端友好提示
        return jsonify({"error": f"文件读取失败：{exc}"}), 400

    if df.empty:
        return jsonify({"error": "文件里没有数据"}), 400

    # 基础预处理：列名标准化 + 去文本空格 + 数值列转数字，方便后续对话引用
    df = pipeline.normalize_column_names(df)
    df = pipeline.trim_text_columns(df)
    df = pipeline.convert_types(df)
    # 保证列名唯一，避免 JSON 序列化出错
    seen: dict[str, int] = {}
    new_cols = []
    for c in df.columns:
        base = c
        if c in seen:
            seen[c] += 1
            base = f"{c}_{seen[c]}"
        else:
            seen[c] = 0
        new_cols.append(base)
    df.columns = new_cols

    session_id = uuid.uuid4().hex
    STORAGE[session_id] = {
        "df": df,
        "original": df.copy(),
        "history": [],
        "steps": [],
        "filename": file.filename,
    }
    persist_session(session_id)

    mode = "已启用大模型理解" if get_deepseek_key() else "当前为规则模式（设置 DEEPSEEK_API_KEY 可启用大模型）"
    return jsonify({
        "session_id": session_id,
        "filename": file.filename,
        "rows": len(df),
        "columns": list(df.columns),
        "preview": make_preview(df),
        "message": (
            f"已导入“{file.filename}”，共 {len(df)} 行、{len(df.columns)} 列。\n"
            f"已做基础处理：列名标准化、清理文本空格、数值列转为数字。\n"
            f"列名：{'、'.join(df.columns)}\n"
            f"{mode}。你可以开始对话了。"
        ),
    })


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    message = (data.get("message") or "").strip()

    session = get_session(session_id)
    if session is None:
        return jsonify({"error": "会话不存在或已过期"}), 400
    if not message:
        return jsonify({"error": "消息不能为空"}), 400

    result = handle_message(session, message)

    # 记录对话历史（多轮记忆），只保留最近几轮以控制 token
    session.setdefault("history", []).append({"role": "user", "content": message})
    session.setdefault("history", []).append({"role": "assistant", "content": result["reply"]})
    session["history"] = session["history"][-12:]

    # 记录清洗步骤（用于生成可复用脚本）
    if result.get("changed") and result.get("code"):
        session.setdefault("steps", []).append({"desc": result["reply"], "code": result["code"]})

    persist_session(session_id)

    state = state_of(session)
    preview_n = result.get("preview_n", 10)
    state["preview"] = make_preview(session["df"], preview_n)

    return jsonify({
        "reply": result["reply"],
        "rows": state["rows"],
        "columns": state["columns"],
        "preview": state["preview"],
    })


@app.get("/api/download/<session_id>")
def download(session_id: str):
    session = get_session(session_id)
    if session is None:
        return jsonify({"error": "会话不存在或已过期"}), 400
    df = session["df"]
    buf = io.StringIO()
    df.to_csv(buf, index=False, encoding="utf-8-sig")
    buf.seek(0)
    return send_file(
        io.BytesIO(buf.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="cleaned_data.csv",
    )


@app.get("/api/script/<session_id>")
def export_script(session_id: str):
    session = get_session(session_id)
    if session is None:
        return jsonify({"error": "会话不存在或已过期"}), 400
    text = build_script(session)
    return send_file(
        io.BytesIO(text.encode("utf-8")),
        mimetype="text/x-python",
        as_attachment=True,
        download_name="clean_script.py",
    )


@app.get("/api/sessions")
def sessions_list():
    """列出已持久化的历史会话。"""
    return jsonify(store.list_sessions())


if __name__ == "__main__":
    # 单机演示，关闭 debug 以避免自动重载导致 STORAGE 被清空
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    app.run(host=host, port=port, debug=False)
