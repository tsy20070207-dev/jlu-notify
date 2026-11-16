# -*- coding: utf-8 -*-
"""消息拼装 + 多通道分发 + 熔断。

设计要点（都是被各家推送额度逼出来的）：
  * 多条新通知必须合并成一条消息 —— PushPlus 同内容 1 小时限 3 条、
    WxPusher/企微也都有频率限制，一条一条发必然触发限流；
  * 超长就少放几条，剩下的留在队列里下一轮发，绝不截断正文；
  * 单通道失败不影响其它通道；令牌类错误（fatal）记 1 小时熔断，
    避免每 5 分钟拿着错 token 去撞一次接口。
"""

import time

from common import load_json, log, now_ts, pj, save_json

import channels

STATE_PATH = pj("data", "channel_state.json")
FATAL_COOLDOWN = 3600      # 熔断 1 小时


# ---------------------------------------------------------------- 时间窗口

def _minutes_of(text, default=-1):
    try:
        hour, minute = str(text).split(":")
        hour, minute = int(hour), int(minute)
        if 0 <= hour <= 24 and 0 <= minute < 60:
            return hour * 60 + minute
    except Exception:
        pass
    return default


def parse_hhmm(text, default=-1):
    """把 "22:30" 解析成「当天第几分钟」。写错就用 default（对外公开的包装）。"""
    return _minutes_of(text, default)


def channel_char_cap(cfg):
    """启用通道里最小的正文上限（没有配置上限的通道不计）；一个都没有返回 None。"""
    channels_cfg = (cfg or {}).get("channels") or {}
    caps = []
    for name in channels.active(channels_cfg):
        try:
            cap = int((channels_cfg.get(name) or {}).get("max_chars") or 0)
        except (TypeError, ValueError):
            cap = 0
        if cap > 0:
            caps.append(cap)
    return min(caps) if caps else None


def in_quiet_hours(quiet_hours, ts=None):
    """免打扰判断，支持跨零点（如 23:30 → 07:00）。配置不合法就当作不静音。"""
    try:
        start, end = list(quiet_hours or [])[:2]
    except (TypeError, ValueError):
        return False
    s, e = _minutes_of(start), _minutes_of(end)
    if s < 0 or e < 0 or s == e:
        return False
    tm = time.localtime(ts if ts is not None else now_ts())
    cur = tm.tm_hour * 60 + tm.tm_min
    if s < e:
        return s <= cur < e
    return cur >= s or cur < e


# ---------------------------------------------------------------- 拼消息

def build_message(items, cfg):
    """把若干条通知拼成一条 markdown，返回 (text, used_items, summary)。

    used_items 是「真的塞进这条消息」的条目——发出去之后只把它们标成已推送，
    放不下的留在队列里，下一轮再发。
    """
    cfg = cfg or {}
    push = cfg.get("push") or {}
    prefix = push.get("title_prefix") or "吉大通知"
    budget = int(push.get("max_chars") or 1800)
    max_items = int(push.get("max_items") or 8)

    items = [i for i in (items or []) if i]
    if not items:
        return "", [], prefix

    blocks, used, size = [], [], 0
    for item in items:
        if len(used) >= max_items:
            break
        title = (item.get("title") or "").strip() or "（无标题）"
        url = (item.get("url") or "").strip()
        meta = " · ".join([x for x in [(item.get("source") or "").strip(),
                                       (item.get("date") or "").strip()] if x])
        index = len(used) + 1
        block = "**{}. {}**\n[{}]({})\n\n".format(index, title, meta or "查看原文", url)
        if size + len(block) > budget and used:
            break
        blocks.append(block)
        used.append(item)
        size += len(block)

    left = len(items) - len(used)
    head = "**{} · {} 条新通知**\n\n".format(prefix, len(used))
    text = head + "".join(blocks)
    if left > 0:
        text += "…… 还有 {} 条，下一轮继续推\n".format(left)
    summary = "{} · {} 条新通知".format(prefix, len(used))
    return text.rstrip(), used, summary


def build_digest(items, cfg, day_label="", budget=None, extra=0):
    """把一天的通知拼成一条汇总，返回 (text, summary, 总条数)。

    先排「带链接」的若干条（不超过预算的 65%），剩下的降级成「仅标题」清单，
    把剩余预算用完 —— 这样一天再多条也不会漏，只是后面的不带链接。
    extra 是「被 max_items 挡在外面、连标题都没排进来的条数」，只为把总数说清楚。
    """
    cfg = cfg or {}
    digest_cfg = cfg.get("digest") or {}
    push_cfg = cfg.get("push") or {}
    prefix = push_cfg.get("title_prefix") or "吉大通知"
    budget = max(400, int(budget or digest_cfg.get("max_chars") or 1700))
    max_items = max(1, int(digest_cfg.get("max_items") or 40))

    items = [i for i in (items or []) if i][:max_items]
    if not items:
        return "", "", 0
    extra = max(0, int(extra or 0))
    total = len(items) + extra
    items = items[::-1]                       # 最新的排最前面
    label = day_label or time.strftime("%m月%d日")

    header = "**{} · {}汇总（{} 条）**\n\n".format(prefix, label, total)
    link_cap = int(budget * 0.65)
    blocks, size = [], len(header)
    for index, item in enumerate(items, 1):
        title = (item.get("title") or "").strip() or "（无标题）"
        url = (item.get("url") or "").strip()
        meta = " · ".join([x for x in [(item.get("source") or "").strip(),
                                       (item.get("date") or "").strip()] if x])
        block = "**{}. {}**\n[{}]({})\n\n".format(index, title, meta or "查看原文", url)
        if blocks and size + len(block) > link_cap:
            break
        blocks.append(block)
        size += len(block)

    rest = items[len(blocks):]
    text = header + "".join(blocks)
    shown = 0
    if rest:
        section = "**其余 {} 条（仅标题）**\n".format(len(rest))
        text += section
        size += len(section)
        for item in rest:
            title = (item.get("title") or "").strip() or "（无标题）"
            source = (item.get("source") or "").strip()
            line = "· {}{}\n".format(title, "（{}）".format(source) if source else "")
            if size + len(line) + 40 > budget:      # 给末尾「还有 N 条」留位置
                break
            text += line
            size += len(line)
            shown += 1

    hidden = (len(rest) - shown) + extra
    if hidden > 0:
        text += "…… 还有 {} 条没列出（白天已单独推送）\n".format(hidden)

    summary = "{} · {}汇总（{} 条）".format(prefix, label, total)
    return text.rstrip(), summary, total


# ---------------------------------------------------------------- 分发

class Notifier(object):
    def __init__(self, cfg, state_path=STATE_PATH):
        self.cfg = cfg or {}
        self.channels_cfg = self.cfg.get("channels") or {}
        self.state_path = state_path
        self.state = load_json(state_path, {}) or {}

    # --- 熔断状态

    def _blocked_reason(self, name):
        record = self.state.get(name) or {}
        fatal_at = int(record.get("fatal_at") or 0)
        if fatal_at and now_ts() - fatal_at < FATAL_COOLDOWN:
            remain = (FATAL_COOLDOWN - (now_ts() - fatal_at)) // 60
            return "{}（约 {} 分钟后重试）".format(record.get("detail") or "配置错误", remain)
        return ""

    def _remember(self, name, result):
        if result.get("fatal"):
            self.state[name] = {"fatal_at": now_ts(), "detail": result.get("detail") or ""}
        elif result.get("ok"):
            self.state.pop(name, None)
        else:
            return
        save_json(self.state_path, self.state)

    # --- 发送

    def send(self, text, summary="", dry_run=False, only=None):
        """逐通道发送，返回结果列表。dry_run=True 时只打印不发送。"""
        results = []
        for name in channels.active(self.channels_cfg):
            if only and name not in only:
                continue
            module = channels.get(name)
            if module is None:
                results.append(channels.make_result(name, skipped=True, detail="未知通道"))
                continue
            blocked = self._blocked_reason(name)
            if blocked:
                results.append(channels.make_result(name, skipped=True,
                                                    detail="熔断中：{}".format(blocked)))
                continue
            if dry_run:
                results.append(channels.make_result(name, skipped=True, detail="dry-run，未真正发送"))
                continue
            try:
                result = module.send(self.channels_cfg.get(name) or {}, text, summary=summary)
            except Exception as exc:
                result = channels.make_result(name, detail="通道异常: {}".format(exc))
            self._remember(name, result)
            results.append(result)
        return results

    def send_items(self, items, dry_run=False, only=None):
        """拼消息 + 发送，返回 (results, text, used_items)。"""
        text, used, summary = build_message(items, self.cfg)
        if not used:
            return [], "", []
        results = self.send(text, summary, dry_run=dry_run, only=only)
        return results, text, used


def log_results(results):
    """把通道结果打到日志里，返回「是否有通道真的成功」。"""
    any_ok = False
    for result in results or []:
        line = channels.describe(result)
        if result.get("ok"):
            any_ok = True
            log(line, "PUSH")
        elif result.get("skipped"):
            log(line, "SKIP")
        else:
            log(line, "WARN")
    return any_ok
