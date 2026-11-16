# -*- coding: utf-8 -*-
"""通道返回值约定。

每个通道暴露 send(cfg, text, summary="", meta=None) -> dict，字段固定：

    channel  通道名
    ok       是否真的发出去了
    skipped  没配置（跳过，不算失败）
    fatal    令牌/配置错，本轮之后都不该再试（熔断）
    detail   给人看的一句话

notifier 按这个约定聚合，一条通道炸了绝不影响另一条。
"""


def make_result(channel, ok=False, skipped=False, fatal=False, detail=""):
    return {
        "channel": channel,
        "ok": bool(ok),
        "skipped": bool(skipped),
        "fatal": bool(fatal),
        "detail": str(detail or ""),
    }


def is_placeholder(value):
    """配置里还是示例占位文字（含中文）→ 当成没填。

    示例配置把「把企微群机器人 webhook 粘到这里」写进了值里，
    直接拿去发会得到 urllib 的 'URL can't contain control characters'，
    新人根本看不懂；这里提前拦下来给一句人话。
    """
    text = str(value or "").strip()
    if not text:
        return False
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def describe(result):
    flag = "OK" if result.get("ok") else ("SKIP" if result.get("skipped") else "FAIL")
    return "[{}] {} {}".format(result.get("channel"), flag, result.get("detail") or "").strip()
