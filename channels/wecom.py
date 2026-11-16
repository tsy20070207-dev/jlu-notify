# -*- coding: utf-8 -*-
"""企业微信群机器人通道。

额度：每个机器人 20 条/分钟、无日上限、免费。
限制：markdown 正文上限 4096 字节（中文 3 字节/字，约 1300 字），按字节截断。
失败码：45009 频率超限（重试无意义，下轮再说）；93000 webhook key 无效（熔断）。
"""

import json

from common import http_post_json, truncate_bytes

from ._base import is_placeholder, make_result

API = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={}"

# 这些错误重试也没用，直接熔断，等用户改配置
FATAL_CODES = (40001, 40013, 93000, 93005)


def endpoint(webhook):
    """webhook 可以填完整 URL，也可以只填 key（更省事、不容易贴错）。"""
    webhook = (webhook or "").strip()
    if not webhook or is_placeholder(webhook):
        return ""
    if webhook.lower().startswith("http"):
        return webhook
    return API.format(webhook)


def send(cfg, text, summary="", meta=None):
    cfg = cfg or {}
    url = endpoint(cfg.get("webhook"))
    if not url:
        hint = "（还是占位符，去 config.json 填真地址）" if is_placeholder(cfg.get("webhook")) else ""
        return make_result("wecom", skipped=True, detail="未配置 webhook，跳过" + hint)

    limit = int(cfg.get("max_chars") or 3800)
    content = truncate_bytes((text or "").strip(), limit)
    payload = {"msgtype": "markdown", "markdown": {"content": content}}

    try:
        status, body = http_post_json(url, payload, timeout=15, retries=1)
    except Exception as exc:
        return make_result("wecom", detail="网络失败: {}".format(exc))

    try:
        data = json.loads(body or "{}")
    except Exception:
        data = {}

    code = data.get("errcode")
    if code == 0:
        return make_result("wecom", ok=True, detail="已送达群机器人")
    if code == 45009:
        return make_result("wecom", detail="触发频率限制(45009)，条目留到下轮")
    if code in FATAL_CODES:
        return make_result("wecom", fatal=True,
                           detail="webhook 无效(errcode={})，请重新复制群机器人地址".format(code))
    return make_result("wecom", detail="errcode={} msg={} http={}".format(
        code, data.get("errmsg"), status))
