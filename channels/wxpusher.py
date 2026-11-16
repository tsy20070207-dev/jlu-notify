# -*- coding: utf-8 -*-
"""WxPusher 通道 —— 免费，把消息推到你的手机 / 电脑。

两种接入方式，二选一；两个都填时用 SPT，app_token 会被忽略：

A. 极简推送 SPT（推荐，不用建应用、不用 appToken / UID）
   1. 打开 <https://wxpusher.zjiecode.com/docs/#/?id=spt> 扫码，拿到 SPT_ 开头的令牌
   2. 装接收端 <https://wxpusher.zjiecode.com/download/>（手机 App 或 Mac/Win 客户端）
   3. 填 config.json 的 channels.wxpusher.spt
   注意：不装客户端收不到 —— 公众号已经不是主通道了。

B. 标准推送 appToken + UID（要给多人群发、管理订阅用户时才需要）
   1. <https://wxpusher.zjiecode.com/admin/> 微信扫码登录 → 应用管理 → 创建应用
      → 复制 appToken（AT_ 开头）
   2. UID 在应用详情的「用户列表」里，或客户端「我的」页面复制（UID_ 开头）
   3. 填 app_token / uids / topic_ids

限制：content ≤ 40000 字符且 ≤ 65535 UTF-8 字节；summary ≤ 100 字符；
      一次最多 10 个 SPT。
错误码：1000 成功；1001 参数/内容不对，或 SPT 不存在（实测也是 1001，只能靠 msg 区分）；
        1002 令牌无效；1005 太频繁；1006 当日超限（条目留到下轮）；
        令牌类错误一律熔断 1 小时，避免每轮拿错令牌撞接口。
"""

import json

from common import http_post_json, truncate_chars

from ._base import is_placeholder, make_result

STANDARD_API = "https://wxpusher.zjiecode.com/api/send/message"
SIMPLE_API = "https://wxpusher.zjiecode.com/api/send/message/simple-push"

MAX_SPT = 10                        # 官方上限：一次最多 10 个 SPT
FATAL_CODES = (1001, 1002)          # 标准推送：参数错 / appToken 错：重试无意义
RETRY_CODES = (1005, 1006)          # 频率 / 当日超限：条目留着下轮发


def parse_spt(cfg):
    """把一个或多个 SPT 归一化成 list。

    支持 "SPT_xxx"、["SPT_a", "SPT_b"]、"SPT_a,SPT_b"（中文逗号/空格也能切），
    自动去空、去重、丢掉示例占位符，最多保留 10 个。

    只认 SPT_ 开头的片段：配置里留着说明文字（比如「SPT_粘这里（见 README）」）
    时会把整串当没填，而不是把 README、1 这种碎片当成令牌发出去。
    """
    raw = (cfg or {}).get("spt")
    if isinstance(raw, (list, tuple)):
        chunks = [str(item or "") for item in raw]
    else:
        text = str(raw or "").strip()
        if not text or is_placeholder(text):     # 整串就是示例文字 → 当没填
            return []
        for sep in ("，", ";", "；", "、", " ", "\t", "\r", "\n"):
            text = text.replace(sep, ",")
        chunks = text.split(",")

    out = []
    for chunk in chunks:
        value = str(chunk or "").strip()
        if (value.startswith("SPT_") and not is_placeholder(value)
                and value not in out):
            out.append(value)
    return out[:MAX_SPT]


def _loads(body):
    try:
        return json.loads(body or "{}")
    except Exception:
        return {}


def _looks_like_token_error(data):
    """判断这条报错是不是「令牌写错了」。

    实测：SPT 不存在时接口返回的是 1001，msg 为
    「用户不存在，请检查你的SPT」，并非文档里的 1002 —— 只能看 msg 兜底。
    """
    msg = str((data or {}).get("msg") or "")
    return any(key in msg for key in ("SPT", "spt", "appToken", "apptoken",
                                      "用户不存在", "不存在", "token", "Token"))


def _send_simple(spts, text, summary, cfg):
    """极简推送：POST /api/send/message/simple-push。"""
    limit = int(cfg.get("max_chars") or 1700)
    base = {
        "content": truncate_chars((text or "").strip(), limit),
        "summary": truncate_chars(summary or "吉大通知", 100, "…"),
    }
    if len(spts) == 1:
        base["spt"] = spts[0]
    else:
        base["sptList"] = spts

    # 官方 SPT 示例只用过 contentType=1（纯文本）。先按 markdown 发，
    # 万一不支持（1001 且与令牌无关）就自动降级纯文本重发一次 —— 只降一次。
    for content_type in (3, 1):
        payload = dict(base, contentType=content_type)
        try:
            status, body = http_post_json(SIMPLE_API, payload, timeout=15, retries=1)
        except Exception as exc:
            return make_result("wxpusher", detail="网络失败: {}".format(exc))

        data = _loads(body)
        code = data.get("code")
        if code == 1000:
            note = "" if content_type == 3 else "（接口不支持 markdown，已降级纯文本）"
            return make_result("wxpusher", ok=True,
                               detail="已送达 WxPusher（{} 个 SPT）{}".format(len(spts), note))
        if code in RETRY_CODES:
            return make_result("wxpusher", detail="{}({})，条目留到下轮".format(
                data.get("msg"), code))
        if code == 1002 or (code == 1001 and _looks_like_token_error(data)):
            return make_result("wxpusher", fatal=True,
                               detail="SPT 无效({})：{}；重新扫码拿一个 SPT_ 令牌".format(
                                   code, data.get("msg") or ""))
        if code == 1001 and content_type == 3:
            continue                                # 换纯文本再试一次
        if code == 1001:
            return make_result("wxpusher", detail="内容超长或参数不对(1001)：{}；"
                                                  "把 channels.wxpusher.max_chars 调小试试".format(
                                                      data.get("msg") or ""))
        return make_result("wxpusher", detail="code={} msg={} http={}".format(
            code, data.get("msg"), status))

    return make_result("wxpusher", detail="markdown 和纯文本都被拒了，检查 content 是否超限")


def _send_standard(cfg, token, uids, topics, text, summary):
    """标准推送：POST /api/send/message，靠 appToken + uids / topicIds。"""
    limit = int(cfg.get("max_chars") or 1700)
    payload = {
        "appToken": token,
        "content": truncate_chars((text or "").strip(), limit),
        "summary": truncate_chars(summary or "吉大通知", 20, "…"),
        "contentType": 3,
        "uids": uids,
        "topicIds": topics,
    }

    try:
        status, body = http_post_json(STANDARD_API, payload, timeout=15, retries=1)
    except Exception as exc:
        return make_result("wxpusher", detail="网络失败: {}".format(exc))

    data = _loads(body)
    code = data.get("code")
    if code == 1000:
        targets = len(uids) + len(topics)
        return make_result("wxpusher", ok=True,
                           detail="已送达微信（{} 个接收目标）".format(targets))
    if code in RETRY_CODES:
        return make_result("wxpusher", detail="{}({})，条目留到下轮".format(
            data.get("msg"), code))
    if code in FATAL_CODES:
        return make_result("wxpusher", fatal=True,
                           detail="{}({})，检查 appToken / UID 是否复制完整".format(
                               data.get("msg"), code))
    return make_result("wxpusher", detail="code={} msg={} http={}".format(
        code, data.get("msg"), status))


def send(cfg, text, summary="", meta=None):
    cfg = cfg or {}

    spts = parse_spt(cfg)
    if spts:                                    # SPT 优先，app_token 直接忽略
        return _send_simple(spts, text, summary, cfg)

    token = (cfg.get("app_token") or "").strip()
    uids = [str(u).strip() for u in (cfg.get("uids") or [])
            if str(u).strip() and not is_placeholder(u)]
    topics = [t for t in (cfg.get("topic_ids") or []) if t]

    if not token or is_placeholder(token):
        hint = ("（还是占位符：spt 要以 SPT_ 开头，app_token 以 AT_ 开头）"
                if is_placeholder(token) else "")
        return make_result("wxpusher", skipped=True,
                           detail="未配置 spt / app_token，跳过" + hint)
    if not uids and not topics:
        return make_result("wxpusher", skipped=True,
                           detail="未配置 uids / topic_ids，跳过"
                                  "（后台应用详情的用户列表里复制 UID_ 开头那串）")

    return _send_standard(cfg, token, uids, topics, text, summary)

