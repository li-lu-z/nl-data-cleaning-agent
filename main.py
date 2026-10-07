"""数据清洗与查询脚本入口。

流程：读取原始数据 -> 清洗 -> 查询/筛选 -> 写出结果 -> 打印报告。

用法示例：
    python main.py                          # 清洗并输出全部数据
    python main.py --date 2026.10.6         # 按某一天筛选
    python main.py --start 2026.10.1 --end 2026.10.31
    python main.py --where 城市=上海 --where 状态=已签收
    python main.py --date 2026.10.6 --out result.csv
"""

import argparse
import sys

from cleaner.config import load_config
from cleaner import pipeline, query


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="数据清洗与查询脚本")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument(
        "--date-column",
        default=None,
        help="日期列名（默认取配置 query.date_column）",
    )
    parser.add_argument("--date", default=None, help="筛选某一天，如 2026.10.6")
    parser.add_argument("--start", default=None, help="筛选开始日期，如 2026.10.1")
    parser.add_argument("--end", default=None, help="筛选结束日期，如 2026.10.31")
    parser.add_argument(
        "--where",
        action="append",
        default=[],
        help="按列精确匹配，如 --where 城市=上海（可多次使用）",
    )
    parser.add_argument("--out", default=None, help="输出文件名（如 result.csv）")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)

    raw_path = cfg["source"]
    print(f"[1/4] 读取原始数据：{raw_path}")
    df = pipeline.read_source(raw_path)
    print(f"      原始数据：{len(df)} 行 x {len(df.columns)} 列")

    print("[2/4] 执行清洗规则...")
    cleaned = pipeline.clean(df, cfg.get("cleaning", {}))
    print(f"      清洗后：{len(cleaned)} 行 x {len(cleaned.columns)} 列")

    print("[3/4] 执行查询/筛选...")
    result = cleaned
    date_column = args.date_column or cfg.get("query", {}).get("date_column", "下单时间")

    if args.date or args.start or args.end:
        result = query.filter_by_date(
            result,
            date_column,
            date=args.date,
            start=args.start,
            end=args.end,
        )
    if args.where:
        result = query.filter_by_kv(result, args.where)
    print(f"      筛选后：{len(result)} 行")

    out_path = pipeline.write_output(
        result,
        cfg.get("output_dir", "data/clean"),
        cfg.get("output_format", "csv"),
        args.out,
    )
    print(f"[4/4] 完成，结果已写入：{out_path}")

    print("清洗报告：")
    print(f"  - 清洗删除行数：{len(df) - len(cleaned)}")
    print(f"  - 筛选删除行数：{len(cleaned) - len(result)}")
    print(f"  - 最终保留行数：{len(result)}")

    if len(result) and (args.date or args.start or args.end or args.where):
        print("命中结果预览（最多 20 行）：")
        print(result.head(20).to_string(index=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # 顶层统一兜底，方便自动化脚本看到友好报错
        print(f"运行失败：{exc}", file=sys.stderr)
        sys.exit(1)
