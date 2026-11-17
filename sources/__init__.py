# -*- coding: utf-8 -*-
"""源类型注册表。

sources.json 里每条记录用 "type" 指定抓取方式，默认 site：
    {"name": "本科生院", "url": "https://jwc.jlu.edu.cn/zxzx/tzgg.htm", "level": "A", "type": "site"}
    {"name": "OA·校内通知", "type": "oa", "channel_id": "179577", "pages": 2,
     "needs_campus": true, "level": "A"}
"""

from . import oa, site                       # noqa: F401

FETCHERS = {
    "site": site,
    "oa": oa,
}


class UnknownSourceType(Exception):
    pass


def fetch(source, request_cfg=None):
    """按 type 抓一个源，返回 [{"title","url","date","source"}]。"""
    kind = ((source or {}).get("type") or "site").strip().lower()
    fetcher = FETCHERS.get(kind)
    if fetcher is None:
        raise UnknownSourceType("未知的源类型: {}（可用: {}）".format(
            kind, ", ".join(sorted(FETCHERS))))
    return fetcher.fetch(source, request_cfg)


def kinds():
    return sorted(FETCHERS)
