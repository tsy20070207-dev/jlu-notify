# -*- coding: utf-8 -*-
"""博达（Visual SiteBuilder 9）站点列表页解析。

吉大 100+ 个单位站点基本都跑在博达建站系统上，列表页有三种实测写法：

  A 日期在后  <li><a href="../info/1095/58371.htm">标题</a><span>2026-10-13</span></li>
  B 日期在前  <li><span>2026-10-20</span><a href="../info/1050/12735.htm">标题</a></li>
  C 日期拆分  <li><div class="date"><p class="day">11-11</p><p class="year">2026</p></div>
              <div class="text"><a href="../info/1008/18689.htm" class="tit">标题</a></div></li>

共同点：详情链接都含 /info/<栏目ID>/<文章ID>.htm，标题就是链接文字，日期在同一块里。
所以按「块」切分（li → tr → 整页兜底），块内找链接 + 日期：

  * 不依赖 class / id / 具体样式，站点改版也不容易挂；
  * 不会把相邻条目的日期张冠李戴；
  * 导航链接（块内没有日期）自动被过滤，不用维护黑名单。

实测踩过的坑：化学学院列表页的摘要正文里有「公示时间：2026年11月11日」——
那是正文不是发布日期，所以日期不能全文搜索，必须限定在块内，并且让
day/year 组合标签的优先级高于正文里的完整日期。
"""

import html as html_lib
import re
import time
import urllib.parse

# 博达的详情页链接：../info/1095/58371.htm、/info/1050/12735.html
DETAIL_PATTERN = r"/info/\d+/\d+\.s?html?$"

ANCHOR_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.I)
HREF_RE = re.compile(r"""href\s*=\s*["']([^"']*)["']""", re.I)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
# 2026-10-13 / 2026/1/8 / 2026年11月11日 / 2026.5.25
FULL_DATE_RE = re.compile(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")
# 11-11 / 5/25（只有月日，年份要看旁边的 class="year" 标签）
MD_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[-/.]\s*(\d{1,2})(?!\d)")
YEAR_TAG_RE = re.compile(r"""class\s*=\s*["']?[^"'>]*year[^"'>]*["']?[^>]*>\s*(20\d{2})""", re.I)
PUBLISH_RE = re.compile(
    r"(?:发布|更新|创建|发表)?时间[：:]\s*(20\d{2}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2})")
# 学工部这类把日期拆成「日 + 年月」两段：<p class="time">16<span>2026-11</span></p>
# 物理学院是 <b>12</b><span> / 2026-11</span>，中间还多一个斜杠
DAY_YM_TEXT_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:[/\-.、]\s*)?(20\d{2})\s*[-/.]\s*(\d{1,2})(?!\d)")
# 标题末尾的日期要去掉：标题 [2026-10-13]
TITLE_DATE_SUFFIX_RE = re.compile(
    r"[\s\[\(（【]*20\d{2}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}[\s\]\)）】]*$")
# 标题开头的日期也要去掉：12 / 2026-11 关于……
TITLE_DATE_LEAD_RE = re.compile(
    r"^[\s\[\(（【]*(?:\d{1,2}\s*(?:[/\-.、]\s*)?20\d{2}\s*[-/.]\s*\d{1,2}"
    r"|20\d{2}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2})[\s\]\)）】]*")
HEADING_RE = re.compile(r"(?is)<h[1-4]\b[^>]*>(.*?)</h[1-4]>")

BLOCK_PATTERNS = (
    re.compile(r"<li\b[^>]*>(.*?)</li>", re.S | re.I),
    re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I),
)

# 这些是「更多 / 详情」之类的导航文字，即使带日期也不是通知
SKIP_TITLES = {"更多", "more", "更多>>", "详细", "详情", ">>", "查看详情"}


# ---------------------------------------------------------------- 文本工具

def clean_text(text):
    """去标签 + 反转义 + 折叠空白。"""
    text = TAG_RE.sub(" ", text or "")
    text = html_lib.unescape(text)
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    return SPACE_RE.sub(" ", text).strip()


def normalize_date(text):
    """把 2026-1-8 / 2026/01/08 / 2026年1月8日 统一成 2026-01-08；失败返回空串。"""
    m = FULL_DATE_RE.search(str(text or ""))
    if not m:
        return ""
    year, month, day = m.groups()
    return "{}-{:02d}-{:02d}".format(year, int(month), int(day))


def page_title(html):
    """取 <title>，给自动发现时起名用。"""
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    return clean_text(m.group(1)) if m else ""


def _dates_in(block, anchor_pos, default_year):
    """块内日期候选：[(优先级, 距链接的距离, 'YYYY-MM-DD')]，优先级 0 最可信。"""
    out = []
    year_m = YEAR_TAG_RE.search(block)
    for m in MD_RE.finditer(block):
        month, day = int(m.group(1)), int(m.group(2))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        year = year_m.group(1) if year_m else default_year
        out.append((0 if year_m else 2, abs(m.start() - anchor_pos),
                    "{}-{:02d}-{:02d}".format(year, month, day)))
    for m in FULL_DATE_RE.finditer(block):
        out.append((1, abs(m.start() - anchor_pos), normalize_date(m.group(0))))
    # 日期被包在 <a> 里的 DOM：<p class="time">16<span>2026-11</span></p>
    # 去标签后是「16 2026-11」，所以单独认这一种，优先级最低，不会覆盖上面的
    loose = clean_text(_strip_href(block))
    for m in DAY_YM_TEXT_RE.finditer(loose):
        day, year, month = int(m.group(1)), m.group(2), int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            out.append((3, abs(m.start() - anchor_pos),
                        "{}-{:02d}-{:02d}".format(year, month, day)))
    out.sort(key=lambda item: (item[0], item[1]))
    return out


def _strip_href(block):
    """去掉 <a> 开标签里的 href 值，保留链接文字（日期可能就在链接里面）。"""
    return re.sub(r"""(?is)<a\b([^>]*?)\bhref\s*=\s*["'][^"']*["']([^>]*)>""",
                  r"<a\1\2>", block or "")


def _clean_title(text):
    """标题清洗：去掉首尾的日期残留、装饰性符号。"""
    text = TITLE_DATE_SUFFIX_RE.sub("", text or "")
    text = TITLE_DATE_LEAD_RE.sub("", text)
    return text.strip(" -—|·　…№")


def _anchor_title(anchor_match):
    """<a title="..."> 里的标题（学工部这类把摘要也塞进链接里的站点只能靠它）。"""
    attr_m = re.search(r"""title\s*=\s*["']([^"']+)["']""", anchor_match.group(1), re.I)
    if not attr_m:
        return ""
    candidate = _clean_title(clean_text(attr_m.group(1)))
    if len(candidate) >= 6 and candidate.lower() not in SKIP_TITLES:
        return candidate
    return ""


def _title_of(block, anchor_match):
    """标题优先级：<a title> → 块内 h1~h4 → 链接文字。

    物理学院把标题放在 <h3> 里、日期放在链接里，用链接文字会得到
    「12 / 2026-11 关于申报…」，所以标题标签要优先于链接文字。
    """
    attr_title = _anchor_title(anchor_match)
    if attr_title:
        return attr_title
    for m in HEADING_RE.finditer(block):
        text = _clean_title(clean_text(m.group(1)))
        if 6 <= len(text) <= 120:
            return text
    return _clean_title(clean_text(anchor_match.group(2)))


# ---------------------------------------------------------------- 列表页

def parse_list(html, base_url, detail_pattern=DETAIL_PATTERN, default_year=None, limit=0):
    """从列表页 HTML 抽条目：[{"title", "url", "date"}]，保持页面顺序。

    base_url 用来把 ../info/1095/58371.htm 补成绝对地址；
    limit > 0 时只取前 limit 条（首次建基线时省事）。
    """
    if not html:
        return []
    default_year = str(default_year or time.strftime("%Y"))
    detail_re = re.compile(detail_pattern or DETAIL_PATTERN, re.I)

    def pick(scope):
        found = []
        for m in ANCHOR_RE.finditer(scope):
            href_m = HREF_RE.search(m.group(1))
            if not href_m:
                continue
            href = (href_m.group(1) or "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            url = urllib.parse.urljoin(base_url, href)
            if not detail_re.search(urllib.parse.urlparse(url).path):
                continue
            title = _title_of(scope, m)
            if not title or len(title) < 3 or title.lower() in SKIP_TITLES:
                continue
            date = ""
            for _pri, _dist, value in _dates_in(scope, m.start(), default_year):
                date = value
                break
            if not date:
                continue  # 块内没有日期 → 导航 / 图片位，不是通知条目
            found.append({"title": title[:200], "url": url, "date": date})
        return found

    items, seen = [], set()

    def merge(batch):
        for item in batch:
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            items.append(item)

    # 依次尝试 li / tr 块；某一种能解析出条目就不再换结构（避免重复扫全页）
    for pattern in BLOCK_PATTERNS:
        blocks = [m.group(1) for m in pattern.finditer(html)]
        if not blocks:
            continue
        for block in blocks:
            merge(pick(block))
            if limit and len(items) >= limit:
                return items
        if items:
            return items

    # 兜底：少数站点不用 li/tr，直接 div 平铺，整页扫一遍
    merge(pick(html))
    return items[:limit] if limit else items


def parse_detail_time(html, head_chars=8000):
    """详情页头部的「时间：2026-10-13」→ 2026-10-13（拿不到返回空串）。

    只看前 head_chars 个字符：正文里常有「报名时间：」「公示时间：」，
    那是内容不是发布时间。列表页的日期才是权威，这里只是补充。
    """
    text = clean_text(html)[:int(head_chars)]
    m = PUBLISH_RE.search(text)
    return normalize_date(m.group(1)) if m else ""
