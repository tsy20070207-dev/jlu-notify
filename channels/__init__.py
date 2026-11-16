# -*- coding: utf-8 -*-
"""推送通道注册表。

加通道只要三步：新建 channels/xxx.py 实现 send()，在这里 import + 注册，
再在 config.py 的 DEFAULT_CONFIG["channels"] 里加默认配置。
"""

from . import pushplus, wecom, wxpusher          # noqa: F401
from ._base import describe, make_result        # noqa: F401

REGISTRY = {
    "wecom": wecom,
    "wxpusher": wxpusher,
    "pushplus": pushplus,
}

# 发送顺序：企微群机器人最稳，放第一个
ORDER = ("wecom", "wxpusher", "pushplus")


def get(name):
    return REGISTRY.get(name)


def names():
    return list(ORDER)


def active(channels_cfg):
    """按配置筛出启用的通道名（顺序稳定）。"""
    channels_cfg = channels_cfg or {}
    return [n for n in ORDER if (channels_cfg.get(n) or {}).get("enabled")]
