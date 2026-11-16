# -*- coding: utf-8 -*-
"""配置结构定义与读写。

config.json  —— 只放「你自己的东西」：推送令牌、轮询频率、免打扰时段
sources.json —— 抓哪些栏目（由 discover.py 生成，可手改）
两者分开，git 只提交 *.example.json，真实令牌永远不进仓库。
"""

import os

from common import load_json, pj, save_json

CONFIG_PATH = pj("config.json")
SOURCES_PATH = pj("sources.json")

DEFAULT_CONFIG = {
    "db_path": "data/notify.db",
    "request": {
        "timeout": 15,
        "retries": 2,
        # 同时抓几个源。别调太大，学校服务器也不容易
        "workers": 4,
        # 每次请求前的随机抖动（毫秒），避免所有源同一秒开火
        "jitter_ms": [0, 1500],
    },
    "schedule": {
        # A 级源：学校通知公告、本科生院、你的学院…… 5 分钟
        "level_a_minutes": 5,
        # B 级源：其余院系 / 机构 30 分钟
        "level_b_minutes": 30,
        # 免打扰时段：这段时间不推送，条目留在队列里，早上合并成一条发
        "quiet_hours": ["23:30", "07:00"],
        # 单次运行的时间上限（秒），防止 launchd 把两次运行叠在一起
        "max_run_seconds": 240,
    },
    "push": {
        "merge": True,
        # 一条消息最多放几条通知，超出的留到下一轮
        "max_items": 8,
        # 正文长度上限（字符），按各通道再截断一次
        "max_chars": 1800,
        "title_prefix": "吉大通知",
        # 首次抓到某个源时只建基线、不推送（避免新装当天被 30 条历史通知轰炸）
        "first_run_baseline_only": True,
        # 单次运行最多推送多少条（防止某源改版后一次刷出几百条）
        "max_per_run": 60,
    },
    "channels": {
        "wecom": {
            "enabled": True,
            "webhook": "",
            "max_chars": 3800,
        },
        "wxpusher": {
            "enabled": True,
            # 极简推送：扫 https://wxpusher.zjiecode.com/docs/#/?id=spt 拿 SPT_ 令牌
            # 填了它就不用 app_token / uids（两个都填时 SPT 优先）
            "spt": "",
            # 标准推送：后台建应用拿 appToken，再填订阅用户的 UID
            "app_token": "",
            "uids": [],
            "topic_ids": [],
            "max_chars": 1700,
        },
        "pushplus": {
            # 免费额度需要实名，默认关闭；填了 token 再打开
            "enabled": False,
            "token": "",
            "topic": "",
            "max_chars": 1700,
        },
    },
}


def deep_merge(base, extra):
    """递归合并，extra 优先，返回新 dict（不修改入参）。"""
    out = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path=None):
    """读 config.json，缺失字段用默认值补全（不写回）。"""
    path = path or CONFIG_PATH
    cfg = deep_merge(DEFAULT_CONFIG, load_json(path, {}) or {})
    if not os.path.isabs(str(cfg.get("db_path") or "")):
        cfg["db_path"] = pj(str(cfg.get("db_path") or "data/notify.db"))
    return cfg


def write_config(cfg, path=None):
    save_json(path or CONFIG_PATH, cfg)


def load_sources(path=None):
    """读 sources.json，返回规范化后的源列表。"""
    raw = load_json(path or SOURCES_PATH, {}) or {}
    items = raw.get("sources") if isinstance(raw, dict) else raw
    out = []
    for idx, item in enumerate(items or []):
        if not isinstance(item, dict) or not item.get("url"):
            continue
        source = dict(item)
        source.setdefault("name", "源{}".format(idx + 1))
        source.setdefault("level", "B")
        source.setdefault("enabled", True)
        source.setdefault("detail_pattern", r"/info/\d+/\d+\.s?html?$")
        out.append(source)
    return out


def enabled_sources(sources):
    return [s for s in sources if s.get("enabled", True)]
