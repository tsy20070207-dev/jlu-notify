#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动发现全校单位站点的「通知公告」栏目，生成候选清单。

用法：
    python3 discover.py                     # 用学校主页的机构名单（118 个站点）
    python3 discover.py --write             # 直接生成 sources.json（每站取最佳候选）
    python3 discover.py --reuse --write     # 不联网，用上次的候选清单重排 sources.json
    python3 discover.py --domains chem.jlu.edu.cn law.jlu.edu.cn
    python3 discover.py --from-file my.txt  # 每行一个域名

原理（都是实测出来的）：
  1. 从 www.jlu.edu.cn/jgsz/yxsz.htm + gl_fwyywjg.htm 抓全校单位站点名单；
  2. 每个站点首页抓一次，收集所有文件名里含 gg / tz 的栏目链接
     （tzgg、xxgg、jwgg、yngg、jxtz 都算，电子学院就叫 jwgg）；
     首页没有就再试 index/tzgg.htm 这类惯例路径（吉大统一用 tzgg 命名）；
  3. 每个候选栏目页都真抓一次、用 parse_list 数条目，再按
     「通知类 > 其它 > 新闻类」→「非党建」→「路径浅」→「条目多」排序。
     必须这样排：化学院 xwtz/ 下既有 xwdt（新闻）又有 tzgg（通知），
     光看条目数会把「新闻动态」当主栏目；研究生院的 pygz1/jxggyyj.htm
     条目和 tzgg.htm 一样多，但后者才是主栏目。
  4. 输出 sources_candidates.json 供确认，确认后 --write 生成 sources.json。

一次全量探测约 600 个请求、1 分钟，是**一次性**操作，别放进定时任务。
"""

import argparse
import concurrent.futures as cf
import re
import urllib.parse

import parser
from common import FetchError, http_get, load_json, log, pj, save_json, ts_text

UNIT_PAGES = (
    "https://www.jlu.edu.cn/jgsz/yxsz.htm",
    "https://www.jlu.edu.cn/jgsz/gl_fwyywjg.htm",
)
DOMAIN_RE = re.compile(r"https?://([a-z0-9][a-z0-9.-]*\.jlu\.edu\.cn)", re.I)
HREF_RE = re.compile(r"""href\s*=\s*["']([^"']*?(?:gg|tz)[^"']*?\.s?html?)["']""", re.I)
FALLBACK_PATHS = ("index/tzgg.htm", "xwzx/tzgg.htm", "tzgg.htm", "xxgg.htm",
                  "xwtz/tzgg.htm", "xwgg/tzgg.htm", "xwdt/tzgg.htm")
SKIP_DOMAINS = {"vpn.jlu.edu.cn", "i.jlu.edu.cn"}
CANDIDATES_FILE = "sources_candidates.json"
# 学校主页不会出现在自己的「机构设置」名单里，必须显式加上
EXTRA_DOMAINS = ("www.jlu.edu.cn",)
# 同分时压低这些栏目：党建通知条数常常最多，但不是学生要的主栏目
DEMOTE_PATHS = ("djgz", "dangjian", "zzjs", "djdt", "/dj/", "qzlx")
# 栏目类型：通知类 > 其它 > 新闻类
NOTICE_HINTS = ("tzgg", "ggtz", "xxgg", "jwgg", "yngg", "jxtz", "ggw", "ggl",
                "tzg", "tz", "gg")
NEWS_HINTS = ("xwdt", "xwzx", "news", "xyxw", "kydt", "gzdt", "xwlb", "dtxx")
# 全校学生都得盯着这几个，自动升为 A 级（5 分钟）
CORE_DOMAINS = {"www.jlu.edu.cn", "jwc.jlu.edu.cn", "xsc.jlu.edu.cn",
                "yjsy.jlu.edu.cn", "zsb.jlu.edu.cn"}


def unit_domains():
    """从学校主页的机构设置页抓全部单位子站域名。"""
    domains = set()
    for page in UNIT_PAGES:
        try:
            resp = http_get(page, timeout=20, retries=1)
        except FetchError as exc:
            log("单位名单页抓不到 {}: {}".format(page, exc), "WARN")
            continue
        for match in DOMAIN_RE.finditer(resp.text):
            host = match.group(1).lower()
            if host in SKIP_DOMAINS:
                continue
            domains.add(host)
    return sorted(domains)


def read_domains(path):
    out = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(re.sub(r"^https?://", "", line).split("/")[0].lower())
    return sorted(set(out))


def score_list(url):
    """抓一个候选栏目页，数出能解析到多少条通知。"""
    out = {"url": url, "count": 0, "title": "", "error": ""}
    try:
        resp = http_get(url, timeout=12, retries=0)
    except FetchError as exc:
        out["error"] = str(exc)[:80]
        return out
    if not resp.ok:
        out["error"] = "HTTP {}".format(resp.status)
        return out
    out["title"] = parser.page_title(resp.text)
    out["count"] = len(parser.parse_list(resp.text, base_url=resp.url or url))
    if not out["count"]:
        out["error"] = "页面能打开但解析不出条目"
    return out

def discover_one(domain):
    """探测单个站点：返回 {"domain","title","candidates","error"}。"""
    base = "https://{}/".format(domain)
    record = {"domain": domain, "title": "", "candidates": [], "error": ""}
    try:
        home = http_get(base, timeout=12, retries=0)
    except FetchError:
        record["error"] = "首页抓不到（多半是内网或已下线）"
        return record
    if not home.ok:
        record["error"] = "首页 HTTP {}".format(home.status)
        return record

    record["title"] = parser.page_title(home.text)
    urls = []
    for match in HREF_RE.finditer(home.text):
        href = match.group(1).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        url = urllib.parse.urljoin(home.url or base, href)
        # 只要本站的链接：首页常有兄弟院校 / 友情链接，别把别人的栏目收进来
        if urllib.parse.urlparse(url).netloc.lower() != domain:
            continue
        if url not in urls:
            urls.append(url)

    # 首页没有 gg/tz 类链接的站点（栏目名不这么叫），按惯例路径再试
    if not urls:
        for path in FALLBACK_PATHS:
            url = urllib.parse.urljoin(base, path)
            if url not in urls:
                urls.append(url)

    for url in urls[:6]:
        record["candidates"].append(score_list(url))
    return record


def pick_best(record, min_count=1):
    """候选里挑最像「主通知栏目」的那个。

    排序键（从高到低）：
      1. 通知类栏目（tzgg / xxgg / jwgg …）优先于新闻类（xwdt / xwzx / news）——
         化学学院 xwdt 有 20 条、tzgg 有 15 条，但你要的是通知；
      2. 非党建栏目优先 —— 法学院 djgz/tzgg 有 16 条，可它不是主栏目；
      3. 路径浅的优先 —— yjsy.jlu.edu.cn/tzgg.htm 比 pygz1/jxggyyj.htm 更像主栏目；
      4. 条目多的优先（避免选到只有 3 条的子栏目）；
      5. 同分取首页里出现得早的。
    """
    candidates = record.get("candidates") or []
    usable = [c for c in candidates if int(c.get("count") or 0) >= min_count]
    if not usable:
        return None

    def rank(candidate):
        url = (candidate.get("url") or "").lower()
        path = urllib.parse.urlparse(url).path
        # 只看文件名：/xwtz/xwdt.htm 是新闻，/xwtz/tzgg.htm 是通知，
        # 拿整条路径判断会把 xwtz（新闻通知）误当成通知栏目。
        name = path.rsplit("/", 1)[-1]
        news_like = any(key in name for key in NEWS_HINTS)
        notice_like = any(key in name for key in NOTICE_HINTS)
        tier = 0 if news_like else (2 if notice_like else 1)
        demoted = any(key in url for key in DEMOTE_PATHS)
        depth = len([part for part in path.split("/") if part])
        return (tier,
                0 if demoted else 1,
                -depth,
                int(candidate.get("count") or 0),
                -candidates.index(candidate))

    return max(usable, key=rank)


LEAD_ANNOT_RE = re.compile(r"^[\s\[\(（【<「]+[^\]\)）】>」]{0,10}[\s\]\)）】>」]+")
GENERIC_PARTS = {"吉林大学", "吉林", "吉大", "首页", "主页", "网站主页", "欢迎光临"}


def clean_unit_name(title):
    """从首页 title 榨出单位名：吉林大学-汽车工程学院 → 汽车工程学院。

    实测过几种脏数据：'（重要）吉林-文学院（国际语言学院）'、
    '吉林大学-汽车工程学院'、'吉林大学'。所以先剥掉开头的括号注释，
    再按分隔符切段、丢掉「吉林/吉林大学」这类没信息量的段，最后取最长的一段。
    """
    title = parser.clean_text(title or "")
    title = LEAD_ANNOT_RE.sub("", title)
    title = re.sub(r"^[\s\-—|_、·]+|[\s\-—|_、·]+$", "", title)
    parts = [p.strip(" \t-—|_、·") for p in re.split(r"[\-—|_、]", title)]
    parts = [p for p in parts if p and p not in GENERIC_PARTS]
    name = max(parts, key=len) if parts else title
    name = name.replace("吉林大学", "").strip(" \t-—|_、·")
    return name[:20]


def build_sources(records, min_count=3, all_columns=False):
    """把探测结果转成 sources.json 的源列表。

    返回 (sources, multi)：
      sources —— 源列表；multi —— 存在多个合格栏目的站点 [(单位名, 栏目数), ...]，
      提示用户去 sources_candidates.json 里挑自己需要的栏目。
    """
    sources, used, multi = [], set(), []
    for record in records:
        best = pick_best(record, min_count)
        if not best:
            continue
        unit = clean_unit_name(record.get("title")) or (
            "学校主页" if record["domain"] == "www.jlu.edu.cn" else record["domain"])
        columns = [c for c in (record.get("candidates") or [])
                   if int(c.get("count") or 0) >= min_count]
        if len(columns) > 1:
            multi.append((unit, len(columns)))
        picked = columns if all_columns else [best]

        for candidate in picked:
            if all_columns and len(picked) > 1:
                parts = [p for p in urllib.parse.urlparse(candidate["url"]).path.split("/") if p]
                slug = parts[-2] if len(parts) > 1 else "home"
                name = "{}·{}".format(unit, slug)
            else:
                name = unit
            if name in used:
                name = "{}（{}）".format(name, record["domain"].split(".")[0])
            used.add(name)
            sources.append({
                "name": name,
                "url": candidate["url"],
                "level": "A" if record["domain"] in CORE_DOMAINS else "B",
                "type": "site",
                "enabled": True,
            })
    # 内网 OA 源结构特殊（要手动登录 + 内网），固定带进去但默认停用
    if not any(s.get("type") == "oa" for s in sources):
        sources.append({
            "name": "OA 办公网",
            "url": "http://oa.jlu.edu.cn/",
            "level": "A",
            "type": "oa",
            "enabled": False,
            "_comment": "内网源：先在校内跑 probe_oa.py，再把 enabled 改成 true",
        })
    sources.sort(key=lambda s: (s.get("type") == "oa", s["level"] != "A", s["name"]))
    return sources, multi


def main(argv=None):
    ap = argparse.ArgumentParser(description="发现全校站点的通知栏目")
    ap.add_argument("--domains", nargs="*", help="手工指定域名")
    ap.add_argument("--from-file", help="域名清单文件，每行一个")
    ap.add_argument("--workers", type=int, default=8, help="并发数（默认 8）")
    ap.add_argument("--min-count", type=int, default=3, help="--write 时的最少条目数")
    ap.add_argument("--write", action="store_true", help="生成 sources.json")
    ap.add_argument("--all-columns", action="store_true",
                    help="一个站点有多个通知栏目时全都要（默认只取主栏目）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    ap.add_argument("--reuse", action="store_true",
                    help="不重新联网探测，直接用已有的 sources_candidates.json 重建 sources.json")
    args = ap.parse_args(argv)

    if args.reuse:
        # 改了排序规则想重排一遍时用：省掉几百个请求
        try:
            cached = load_json(CANDIDATES_FILE)
        except (OSError, ValueError) as exc:
            log("读不到 {}，先跑一次完整探测：{}".format(CANDIDATES_FILE, exc), "ERROR")
            return 2
        records = cached.get("units") or []
        log("复用 {}：{} 个站点的探测结果（没联网）".format(CANDIDATES_FILE, len(records)))
        domains = [r["domain"] for r in records]
    elif args.domains:
        domains = sorted({re.sub(r"^https?://", "", d).split("/")[0].lower() for d in args.domains})
    elif args.from_file:
        domains = read_domains(args.from_file)
    else:
        log("从学校主页抓单位名单……")
        domains = sorted(set(unit_domains()) | set(EXTRA_DOMAINS))
    if not domains:
        log("没拿到任何域名，检查网络（校内域名必须直连）", "ERROR")
        return 2

    if not args.reuse:
        records = []
        log("待探测 {} 个站点，并发 {}".format(len(domains), args.workers))
        with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for record in pool.map(discover_one, domains):
                records.append(record)
                best = pick_best(record)
            if best:
                log("{:<26} {:>3} 条  {}".format(record["domain"], best["count"], best["url"]), "DEBUG")
            else:
                log("{:<26} {}".format(record["domain"], record.get("error") or "没找到通知栏目"), "DEBUG")

    found = [r for r in records if pick_best(r)]
    log("探测完成：{} / {} 个站点找到通知栏目".format(len(found), len(records)))

    if not args.dry_run and not args.reuse:
        save_json(pj(CANDIDATES_FILE), {
            "generated_at": ts_text(),
            "unit_count": len(records),
            "found": len(found),
            "units": records,
        })
        log("候选清单已写入 sources_candidates.json")

    if args.write:
        sources, multi = build_sources(records, args.min_count, args.all_columns)
        a_level = sum(1 for s in sources if s["level"] == "A")
        if not args.dry_run:
            save_json(pj("sources.json"), {"generated_at": ts_text(), "sources": sources})
        log("已生成 sources.json：{} 个源（A 级 {} / B 级 {}）".format(
            len(sources), a_level, len(sources) - a_level))
        for src in sources:
            log("  [{}] {} {}".format(src["level"], src["name"], src["url"]), "DEBUG")
        if multi:
            log("有 {} 个站点存在多个通知栏目（默认各取一个）。要全都要就加 --all-columns，"
                "或从 sources_candidates.json 里挑：".format(len(multi)), "WARN")
            for unit, count in multi:
                log("  {} 有 {} 个栏目".format(unit, count), "DEBUG")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

