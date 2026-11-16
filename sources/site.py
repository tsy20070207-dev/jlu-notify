# -*- coding: utf-8 -*-
"""通用站点源：一个列表页 URL → 一批通知条目。

吉大 100+ 个单位站点都是博达（Visual SiteBuilder 9）建的，列表页写法只有
parser.py 里那三种，所以不需要给每个学院写一个爬虫 —— 只要在 sources.json
里登记「栏目 URL + 级别」，加一个源就是加一行配置。

可选字段：
    detail_pattern  详情链接正则，默认 /info/\\d+/\\d+\\.s?html?$
    limit           只取前 N 条（默认 0 = 全取）
    level           A（5 分钟）/ B（30 分钟）
"""

from common import FetchError, http_get, sleep_jitter

import parser


def fetch(source, request_cfg=None):
    source = source or {}
    request_cfg = request_cfg or {}
    url = (source.get("url") or "").strip()
    if not url:
        raise FetchError("源「{}」没配 url".format(source.get("name") or "?"))

    # 每个源开抓前抖一下，别让 80 个请求在同一秒落到学校服务器上
    sleep_jitter(request_cfg.get("jitter_ms"))

    resp = http_get(
        url,
        timeout=int(request_cfg.get("timeout") or 15),
        retries=int(request_cfg.get("retries") or 2),
    )
    if not resp.ok:
        raise FetchError("HTTP {} <- {}".format(resp.status, url))

    items = parser.parse_list(
        resp.text,
        base_url=resp.url or url,
        detail_pattern=source.get("detail_pattern") or parser.DETAIL_PATTERN,
        limit=int(source.get("limit") or 0),
    )
    for item in items:
        item["source"] = source.get("name") or ""
    return items
