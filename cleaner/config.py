"""配置加载模块。"""

import os

import yaml


def load_config(path: str = "config.yaml") -> dict:
    """从 YAML 文件加载清洗配置。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"配置文件不存在：{path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
