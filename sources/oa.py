# -*- coding: utf-8 -*-
"""OA 电子校务平台源 —— oa.jlu.edu.cn，纯内网（10.100.18.18），校外打不开。

四个板块是同一套页面模板（Whir / whirRootPath=/defaultroot），只有 channelId 不同：

    179577 校内通知   179578 学生天地   179579 校内快讯   179581 教学信息

列表页（服务端渲染，不需要 JS，也不需要无头浏览器）：

    http://oa.jlu.edu.cn/defaultroot/PortalInformation!jldxList.action?channelId=179577
    ...?1=1&channelId=179577&startPage=2        翻页，每页 30 条

详情页：

    http://oa.jlu.edu.cn/defaultroot/PortalInformation!getInformation.action?id=72089970&channelId=179577

单条 DOM（标签是大写，正则必须 re.I）：

    <DIV class="li rel"><I></I>
      <A class="font14" href="PortalInformation!getInformation.action?id=...&channelId=...">标题</A>
      ...
      <A class="column" href="...">研究生院</A>
      ...
      <SPAN class="time">今天&nbsp;&nbsp; 10:39</SPAN>
    </DIV>

时间只有三种写法：今天 10:39 / 昨天 16:28 / 2026-11-13。前两种要按本地日期换算，
所以解析函数支持传入 now（测试里固定日期，结果才可复现）。

实测踩过的坑：

  * 不能用 class 前缀匹配 div.li —— 页头有 class="link_r"，会多切出一个假块，
    必须用 \\b 词边界（30 条 → 30 块）；
  * 列表页本身就带标题 + 日期 + 发布单位，不必进详情页，也就绕开了详情页的登录态；
  * 同一条通知会同时挂在校内通知和学生天地，两处 URL 的 channelId 不同，
    所以去重键用 "oa:<id>"（store.item_id 认这个字段），不能只看 URL。
"""

import os
import re
import time

from common import FetchError, http_get, load_json, pj, sleep_jitter

import parser

PLATFORM = "http://oa.jlu.edu.cn"
LIST_ACTION = "/defaultroot/PortalInformation!jldxList.action"
DETAIL_ACTION = "/defaultroot/PortalInformation!getInformation.action"

# channelId → 板块名。sources.json 里以 channel_id 为准，这里只用于报错提示。
BOARDS = (
    ("179577", "校内通知"),
    ("179578", "学生天地"),
    ("179579", "校内快讯"),
    ("179581", "教学信息"),
)

PAGE_SIZE = 30          # 每页 30 条，少于这个数说明到最后一页了

# \bli\b：精确匹配 class="li rel"，不会误吃 class="link_r"
BLOCK_RE = re.compile(
    r"""<div\b[^>]*class\s*=\s*["'][^"']*\bli\b[^"']*["'][^>]*>(.*?)</div>""", re.S | re.I)
TITLE_RE = re.compile(
    r"""<a\b([^>]*class\s*=\s*["'][^"']*\bfont14\b[^"']*["'][^>]*)>(.*?)</a>""", re.S | re.I)
ANCHOR_RE = re.compile(r"""<a\b([^>]*)>(.*?)</a>""", re.S | re.I)
ORG_RE = re.compile(
    r"""<a\b[^>]*class\s*=\s*["'][^"']*\bcolumn\b[^"']*["'][^>]*>(.*?)</a>""", re.S | re.I)
TIME_RE = re.compile(
    r"""<span\b[^>]*class\s*=\s*["'][^"']*\btime\b[^"']*["'][^>]*>(.*?)</span>""", re.S | re.I)
HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
ID_RE = re.compile(r"[?&]id=(\d+)", re.I)
CHANNEL_RE = re.compile(r"[?&]channelId=(\d+)", re.I)
# 今天 10:39 / 昨天 16:28 / 前天 08:11（中间可能是 &nbsp;，clean_text 会折叠掉）
REL_RE = re.compile(r"(今天|昨天|前天)\s*(?:(\d{1,2})\s*[:：]\s*(\d{2}))?")
REL_OFFSET = {"今天": 0, "昨天": -1, "前天": -2}

HINT = ("OA（{}）只能在校内网访问。先在一台连着校园网的机器上用无痕窗口打开\n"
        "    {}\n"
        "能直接看到 30 条列表 = 免登录，直接跑；\n"
        "跳到登录页 = 用 python3 login_once.py 手动登录一次存 cookie，\n"
        "再给该源加 \"cookie_file\": \"data/oa_session.json\"。")


# ---------------------------------------------------------------- URL

def list_url(channel_id, page=1):
    """板块列表页地址。第 2 页起按 OA 自己的翻页链接写（多带一个 1=1 参数）。"""
    vid = str(channel_id or "").strip()
    try:
        page = int(page or 1)
    except (TypeError, ValueError):
        page = 1
    if page <= 1:
        return "{}{}?channelId={}".format(PLATFORM, LIST_ACTION, vid)
    return "{}{}?1=1&channelId={}&startPage={}".format(PLATFORM, LIST_ACTION, vid, page)


def detail_url(item_id, channel_id):
    return "{}{}?id={}&channelId={}".format(
        PLATFORM, DETAIL_ACTION, item_id, str(channel_id or "").strip())


# ---------------------------------------------------------------- 日期

def normalize_date(text, now=None):
    """今天 10:39 / 昨天 16:28 / 2026-11-13 → (YYYY-MM-DD, HH:MM)。

    拿不到日期就返回 ("", "")：调用方按「无日期」处理，不参与过期判断。
    """
    text = parser.clean_text(text or "")
    m = REL_RE.search(text)
    if m:
        ref = now or time.localtime()
        base = time.mktime((ref.tm_year, ref.tm_mon, ref.tm_mday, 0, 0, 0, 0, 0, -1))
        day = base + REL_OFFSET.get(m.group(1), 0) * 86400
        hm = "{:02d}:{}".format(int(m.group(2)), m.group(3)) if m.group(2) else ""
        return time.strftime("%Y-%m-%d", time.localtime(day)), hm
    date = parser.normalize_date(text)
    if not date:
        return "", ""
    hm_m = re.search(r"(\d{1,2}:\d{2})", text)
    return date, hm_m.group(1) if hm_m else ""


def channel_name(html):
    """板块名：var channelName='校内通知' → <span class="tz_tit"> → 面包屑 → 空串。"""
    for pattern in (r"var\s+channelName\s*=\s*'([^']*)'",
                    r'<span[^>]*class\s*=\s*["\'][^"\']*\btz_tit\b[^"\']*["\'][^>]*>(.*?)</span>',
                    r'jldxList\.action\?channelId=\d+["\'][^>]*>([^<]{1,20})</a>'):
        m = re.search(pattern, html or "", re.S | re.I)
        if m:
            name = parser.clean_text(m.group(1))
            if name and name not in ("首页", "更多", "more"):
                return name
    return ""


# ---------------------------------------------------------------- 列表解析

def split_blocks(html):
    """按 div.li 切块，返回块内 HTML 列表（每块正好一条通知）。"""
    return [m.group(1) for m in BLOCK_RE.finditer(html or "")]


def _title_anchor(block):
    """标题链接：优先 class="font14"，结构变了就退化成块内第一个指向详情页的链接。

    两组正则的分组一致（1=标签属性，2=链接文字），所以调用方只看 group(1)/group(2)。
    """
    m = TITLE_RE.search(block or "")
    if m:
        return m
    for m in ANCHOR_RE.finditer(block or ""):
        href_m = HREF_RE.search(m.group(1))
        if href_m and "getInformation.action" in href_m.group(1):
            return m
    return None


def parse_block(block, channel_id, now=None):
    """解析一条：块内没有详情链接 / 标题就返回 None。"""
    title_m = _title_anchor(block)
    if not title_m:
        return None
    href_m = HREF_RE.search(title_m.group(1))
    if not href_m or "getInformation.action" not in href_m.group(1):
        return None
    id_m = ID_RE.search(href_m.group(1))
    if not id_m:
        return None
    title = parser.clean_title(parser.clean_text(title_m.group(2)))
    if not title or title.lower() in parser.SKIP_TITLES:
        return None
    time_m = TIME_RE.search(block)
    date, hm = normalize_date(time_m.group(1) if time_m else "", now)
    org_m = ORG_RE.search(block)
    own_m = CHANNEL_RE.search(href_m.group(1))
    # 详情链接里自带的 channelId 才是权威的（转载过来的条目会保留原板块 id）
    return {
        "title": title,
        "url": detail_url(id_m.group(1), own_m.group(1) if own_m else channel_id),
        "date": date,
        "time": hm,
        "org": parser.clean_text(org_m.group(1)) if org_m else "",
        "dedupe_key": "oa:" + id_m.group(1),
    }


def parse_oa_list(html, channel_id, now=None, limit=0):
    """从 OA 列表页抽条目，保持页面顺序（新的在前）。"""
    items, seen = [], set()
    for block in split_blocks(html):
        item = parse_block(block, channel_id, now)
        if not item or item["dedupe_key"] in seen:
            continue
        seen.add(item["dedupe_key"])
        items.append(item)
        if limit and len(items) >= int(limit):
            break
    return items



# ---------------------------------------------------------------- 抓取

def cookie_of(source):
    """需要登录时，从 cookie_file 里读 Cookie 请求头。

    支持两种写法：{"cookie": "JSESSIONID=..."} 或整个文件就是那一行 Cookie 串。
    login_once.py 存的就是 data/oa_session.json。
    """
    path = str((source or {}).get("cookie_file") or "").strip()
    if not path:
        return ""
    path = path if os.path.isabs(path) else pj(path)
    if not os.path.isfile(path):
        return ""
    data = load_json(path, None)
    if isinstance(data, dict):
        for key in ("cookie", "Cookie", "header", "headers"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict) and value.get("Cookie"):
                return str(value["Cookie"]).strip()
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except Exception:
        return ""


def pages_of(source):
    try:
        return max(1, min(10, int((source or {}).get("pages") or 1)))
    except (TypeError, ValueError):
        return 1


def dump_path():
    return pj("data", "oa_dump.html")


def read_dump(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def fetch(source, request_cfg=None):
    """抓一个 OA 板块，返回 [{"title","url","date","time","org","dedupe_key","source"}]。"""
    source = source or {}
    request_cfg = request_cfg or {}
    name = source.get("name") or "OA"
    channel_id = str(source.get("channel_id") or source.get("channelId") or "").strip()
    if not channel_id:
        raise FetchError("源「{}」没配 channel_id。四个板块是：{}".format(
            name, "、".join("{}={}".format(n, c) for c, n in BOARDS)))

    # 离线回退：显式配了 dump_file 才读本地页面（断网调试用）。
    # 不自动吃 data/oa_dump.html —— 那可能是几个月前 probe_oa.py 留下的旧文件，
    # 悄悄读它会让推送永远停在旧内容，比直接报错难查得多。
    local = str(source.get("dump_file") or "").strip()
    if local and not os.path.isabs(local):
        local = pj(local)
    if local and os.path.isfile(local):
        html = read_dump(local)
        board = channel_name(html)
        items = parse_oa_list(html, channel_id)
        if items:
            for item in items:
                item["source"] = name
                item["board"] = board
            return items

    headers = {}
    cookie = cookie_of(source)
    if cookie:
        headers["Cookie"] = cookie
    timeout = int(request_cfg.get("timeout") or 15)
    retries = int(request_cfg.get("retries") or 1)

    items, seen, board = [], set(), ""
    for page in range(1, pages_of(source) + 1):
        url = (source.get("url") or "").strip() if page == 1 else ""
        url = url or list_url(channel_id, page)
        try:
            sleep_jitter(request_cfg.get("jitter_ms"))
            resp = http_get(url, timeout=timeout, retries=retries, headers=headers)
        except FetchError:
            if page == 1:
                raise FetchError(HINT.format(name, list_url(channel_id)))
            break                          # 第 2 页之后失败不影响已经拿到的
        if not resp.ok:
            if page == 1:
                raise FetchError("HTTP {} <- {}（{}）".format(
                    resp.status, url, "需要登录" if resp.status in (401, 403) else "先确认在校内网"))
            break
        batch = parse_oa_list(resp.text, channel_id)
        if not batch:
            if page == 1:
                raise FetchError(
                    "{} 页面能打开但一条都解析不出（{}）。\n"
                    "把页面存成 data/oa_dump.html 发给我，或先跑 oa_check.py。".format(name, url))
            break
        board = board or channel_name(resp.text)
        for item in batch:
            if item["dedupe_key"] in seen:
                continue
            seen.add(item["dedupe_key"])
            items.append(item)
        if len(batch) < PAGE_SIZE:
            break

    for item in items:
        item["source"] = name
        item["board"] = board or name
    return items

