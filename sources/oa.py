# -*- coding: utf-8 -*-
"""OA（校内办公网）源 —— 只有校园网内可用。

oa.jlu.edu.cn 解析到 10.100.18.18，是纯内网地址。校外（Wi-Fi、5G、Clash
全局代理）一律连不上。手机企微能收到 OA 待办，是因为它走腾讯企微自己的
内网应用代理通道，脚本没法复用那条隧道。

所以这个源有两条路，按你手上有没有常亮内网设备选：

  1. 【推荐】校内常亮设备（宿舍小主机 / 树莓派 / 实验室值班机）直接跑本程序，
     这一支就是普通抓取；
  2. 【先验证】在校内跑一次 probe_oa.py，把 OA 列表页 HTML 存成
     data/oa_dump.html；这里会优先读那个 dump，先把内容推出来。

注意：OA 的详情链接大概率不是 /info/123/456.htm，而 probe_oa.py 的目的就是
把这个格式看清。在拿到真实 DOM 之前，本文件不臆造任何解析规则。
"""

import os

from common import FetchError, http_get, pj

import parser

DEFAULT_URL = "http://oa.jlu.edu.cn/"

HINT = ("OA 只能在校内网访问。请先在一台连着校园网的电脑上运行\n"
        "    python3 probe_oa.py\n"
        "它会打开 OA、由你手动登录，然后把列表页结构存成 data/oa_dump.html；\n"
        "之后本程序就能读那个 dump 推送 OA 通知了。")


def dump_path(path=None):
    return path or pj("data", "oa_dump.html")


def _read_dump(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def fetch(source, request_cfg=None):
    source = source or {}
    request_cfg = request_cfg or {}
    pattern = source.get("detail_pattern") or parser.DETAIL_PATTERN
    base = source.get("base_url") or DEFAULT_URL
    name = source.get("name") or "OA"

    local = source.get("dump_file") or dump_path()
    if local and os.path.isfile(local):
        html = _read_dump(local)
        items = parser.parse_list(html, base_url=base, detail_pattern=pattern)
        if not items:
            raise FetchError(
                "{} 的 dump 里解析不出条目（{}）。\n说明 OA 的 DOM 和博达站点不一样，"
                "把 data/oa_dump.html 发给我，我按真实结构调整解析规则。".format(local, pattern))
        for item in items:
            item["source"] = name
        return items

    url = (source.get("url") or DEFAULT_URL).strip()
    try:
        resp = http_get(url, timeout=int(request_cfg.get("timeout") or 15),
                        retries=int(request_cfg.get("retries") or 1))
    except FetchError:
        raise FetchError(HINT)
    if not resp.ok or "登录" in resp.text[:2000]:
        raise FetchError(HINT)

    items = parser.parse_list(resp.text, base_url=resp.url or url, detail_pattern=pattern)
    if not items:
        raise FetchError("OA 页面能打开但解析不出条目，请运行 probe_oa.py 看清结构。")
    for item in items:
        item["source"] = name
    return items
