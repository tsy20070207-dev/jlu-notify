# -*- coding: utf-8 -*-
"""推送前的降噪闸门（由 cli.finish_and_push 调用）。

要治的四个毛病在这里一次性收口：

  1. 旧通知被当成新通知推 —— 站点改版后列表页日期取不到，几十条历史一股脑进队列；
  2. 单个源刷屏        —— 一个学院一天发 20 条，8 条的额度全被它占了；
  3. 噪音关键词        —— 招标、采购、中标、讣告这类跟你没关系；
  4. 死链              —— 实测约 16% 的单位站点详情页已经 404。

被丢掉的条目不会删除，而是「标记已推送 + 隐藏」：删了的话下一轮抓同一页又会当成
新条目冒出来；隐藏能保证它不会在 22:30 的当日汇总里复活。

超额（per_source_limit）的条目不一样 —— 它们留在队列里，晚上进当日汇总。
所以体验是「实时只推最新几条，其余的在汇总里一次看完」，而不是无声丢掉。
"""

import time
import urllib.error
import urllib.request

from common import USER_AGENT, log

DEFAULT_MAX_AGE_DAYS = 3
DEFAULT_PER_SOURCE_LIMIT = 4
DEAD_STATUS = (404, 410)


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _day_tuple(text):
    """'2026-11-17' → (2026, 11, 17)；解析不出返回 None。"""
    try:
        return time.strptime(str(text or "").strip()[:10], "%Y-%m-%d")[:3]
    except (TypeError, ValueError):
        return None


def _day_ts(day):
    return time.mktime(tuple(day) + (0, 0, 0, 0, 0, -1))


def age_days(date_text, today=None):
    """条目发布日期距今几天；解析不出返回 None（不参与判断）。"""
    day = _day_tuple(date_text)
    if not day:
        return None
    ref = tuple(today)[:3] if today else time.localtime()[:3]
    return int(round((_day_ts(ref) - _day_ts(day)) / 86400))


def is_stale(date_text, max_age_days=0, today=None):
    """发布日期早于「今天 - max_age_days」就算过期；max_age_days<=0 关闭。"""
    max_age_days = _int(max_age_days, 0)
    if max_age_days <= 0:
        return False
    age = age_days(date_text, today)
    return age is not None and age > max_age_days


def select_pushable(items, cfg, today=None):
    """按配置过滤，返回 (kept, hidden)。

    hidden 里每条带 _drop 字段：stale（过期）/ noisy（噪音词），调用方据此打日志、
    并把它们标记成「已推送 + 隐藏」。
    """
    push = (cfg or {}).get("push") or {}
    max_age = _int(push.get("max_age_days", DEFAULT_MAX_AGE_DAYS), DEFAULT_MAX_AGE_DAYS)
    per_source = _int(push.get("per_source_limit", DEFAULT_PER_SOURCE_LIMIT),
                      DEFAULT_PER_SOURCE_LIMIT)
    keywords = [str(k).strip().lower()
                for k in (push.get("title_skip_keywords") or []) if str(k).strip()]

    kept, hidden, used = [], [], {}
    for item in items or []:
        if not item:
            continue
        title = item.get("title") or ""
        if keywords and any(k in title.lower() for k in keywords):
            hidden.append(dict(item, _drop="noisy"))
            continue
        if is_stale(item.get("date"), max_age, today):
            hidden.append(dict(item, _drop="stale"))
            continue
        source = item.get("source") or ""
        if per_source > 0 and used.get(source, 0) >= per_source:
            continue                      # 超额：留在队列，晚上进汇总
        used[source] = used.get(source, 0) + 1
        kept.append(item)
    return kept, hidden


def drop_reasons(hidden):
    """{'stale': 3, 'noisy': 1} —— 给日志用。"""
    out = {}
    for item in hidden or []:
        key = item.get("_drop") or "?"
        out[key] = out.get(key, 0) + 1
    return out


# ---------------------------------------------------------------- 死链抽检

def link_status(url, timeout=6):
    """HEAD 一次拿状态码。连不上 / 站点不支持 HEAD 都返回 0（没结论，别误杀）。"""
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        return int(getattr(exc, "code", 0) or 0)
    except Exception:
        return 0


def is_dead(status):
    return int(status or 0) in DEAD_STATUS


def drop_dead_links(items, cfg=None, limit=None, timeout=6):
    """只抽检「这一轮真要发出去」的那几条（默认 push.max_items 条）。

    返回 (alive, dead)；dead 里每条带 _drop="dead"。检查失败不算死链，
    宁可推出一条能打开的通知，也不要因为一次网络抖动把新通知吞掉。
    """
    push = (cfg or {}).get("push") or {}
    if limit is None:
        limit = _int(push.get("max_items", 8), 8)
    limit = max(0, _int(limit, 8))
    alive, dead = [], []
    for index, item in enumerate([i for i in (items or []) if i]):
        if index >= limit or not item.get("url"):
            alive.append(item)
            continue
        status = link_status(item["url"], timeout=timeout)
        if is_dead(status):
            log("死链 {}：{}".format(status, (item.get("title") or item["url"])[:40]), "SKIP")
            dead.append(dict(item, _drop="dead", _status=status))
        else:
            alive.append(item)
    return alive, dead

