# -*- coding: utf-8 -*-
"""PushPlus 通道 —— 代码预留，默认关闭。

为什么不默认开：PushPlus 免费推送要求实名，不想用自己的身份证可以直接不用它。
想用的话：https://www.pushplus.plus 微信扫码 → 一对一推送 → 复制 token →
填到 config.json 的 channels.pushplus.token，并把 enabled 改成 true。

限制（也是我们坚持「多条合并成一条」的原因之一）：
  * 同一用户 1 分钟最多 5 次
  * 相同内容 1 小时内最多 3 次
错误码：900 token 无效（熔断）；903 含敏感词；905 未实名（熔断）。
"""

import json

from common import http_post_json, truncate_chars

from ._base import is_placeholder, make_result

API = "https://www.pushplus.plus/send"

FATAL_CODES = (900, 905)


def send(cfg, text, summary="", meta=None):
    cfg = cfg or {}
    token = (cfg.get("token") or "").strip()
    if not token or is_placeholder(token):
        hint = "（还是占位符，去 config.json 填真 token）" if is_placeholder(token) else ""
        return make_result("pushplus", skipped=True, detail="未配置 token，跳过" + hint)

    limit = int(cfg.get("max_chars") or 1700)
    payload = {
        "token": token,
        "title": truncate_chars(summary or "吉大通知", 100, "…"),
        "content": truncate_chars((text or "").strip(), limit),
        "template": "markdown",
    }
    topic = (cfg.get("topic") or "").strip()
    if topic:
        payload["topic"] = topic

    try:
        status, body = http_post_json(API, payload, timeout=15, retries=1)
    except Exception as exc:
        return make_result("pushplus", detail="网络失败: {}".format(exc))

    try:
        data = json.loads(body or "{}")
    except Exception:
        data = {}

    code = data.get("code")
    if code == 200:
        return make_result("pushplus", ok=True, detail="已送达")
    if code in FATAL_CODES:
        return make_result("pushplus", fatal=True,
                           detail="{}({})，token 无效或未实名".format(data.get("msg"), code))
    if code == 903:
        return make_result("pushplus", detail="内容含敏感词(903)，请检查标题")
    return make_result("pushplus", detail="code={} msg={} http={}".format(
        code, data.get("msg"), status))
