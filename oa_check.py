#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""摸清 OA 四个板块：要不要登录？板块叫什么？条目长什么样？

在校内网（机房 / 宿舍）跑一条命令就够：

    python3 oa_check.py --write-sources --save-fixtures

它做四件事：
  1. 依次请求 4 个 channelId 的列表页（第一次故意不带任何 cookie，先验证免不免登录）；
  2. 从页面里取板块名（页面里的 var channelName），解析条数与最新 3 条；
  3. --write-sources：把板块名写回 sources.json（只改 type=oa 的 name，其它字段不动）；
  4. --save-fixtures：把 4 份 HTML 存成 tests/fixtures/oa_<channelId>.html 供离线测试。

返回 403 / 跳登录页时会明确提示：用 login_once.py 手动登录一次存 cookie，
再给源加 "cookie_file": "data/oa_session.json"（不自动登录、不碰验证码）。
"""

import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import sources.oa as oa                    # noqa: E402
from common import FetchError, http_get, load_json, pj, save_json   # noqa: E402


def parse_channels(text):
    out = [chunk.strip() for chunk in str(text or "").replace("，", ",").split(",")]
    return [c for c in out if c] or [cid for cid, _ in oa.BOARDS]


def probe(channel_id, timeout=15, cookie_file=""):
    """抓一个板块，返回 dict（抓不到也返回 dict，errors 里放原因）。"""
    url = oa.list_url(channel_id)
    cookie = oa.cookie_of({"cookie_file": cookie_file}) if cookie_file else ""
    headers = {"Cookie": cookie} if cookie else {}
    data = {"channel_id": str(channel_id), "url": url, "status": 0,
            "anonymous": not cookie, "name": "", "count": 0,
            "newest": [], "html": "", "error": ""}
    try:
        resp = http_get(url, timeout=int(timeout), retries=1, headers=headers)
    except FetchError as exc:
        data["error"] = str(exc)
        return data
    data["status"] = resp.status
    data["html"] = resp.text or ""
    data["name"] = oa.channel_name(data["html"])
    if not resp.ok:
        data["error"] = "HTTP {}（{}）".format(
            resp.status, "需要登录" if resp.status in (401, 403) else "先确认在校内网")
        return data
    items = oa.parse_oa_list(data["html"], channel_id)
    data["count"] = len(items)
    data["newest"] = items[:3]
    if not items:
        data["error"] = "页面能打开但一条都解析不出（可能是登录页 / 结构变了）"
    return data


def save_fixture(channel_id, html, fixture_dir=None):
    fixture_dir = fixture_dir or os.path.join(BASE_DIR, "tests", "fixtures")
    os.makedirs(fixture_dir, exist_ok=True)
    path = os.path.join(fixture_dir, "oa_{}.html".format(channel_id))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def source_entries(results):
    """生成 sources.json 里 4 条 OA 源（板块名取不到时用 channelId 兜底）。"""
    entries, used = [], {}
    for data in results:
        cid = data["channel_id"]
        board = data.get("name") or cid
        if board in used:                       # 万一两个板块同名，补上 id 区分
            board = "{}（{}）".format(board, cid)
        used[board] = True
        entries.append({
            "name": "OA·" + board,
            "url": oa.list_url(cid),
            "level": "A",
            "type": "oa",
            "channel_id": cid,
            "pages": 2,
            "needs_campus": True,
            "enabled": True,
        })
    return entries


def write_sources(results, path=None):
    """把板块名写回 sources.json：只改 type=oa 的 name，按 channel_id 匹配。"""
    path = path or pj("sources.json")
    raw = load_json(path, None)
    if not raw:
        return 0
    items = raw.get("sources") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return 0
    names = {entry["channel_id"]: entry["name"] for entry in source_entries(results)}
    changed = 0
    for entry in items:
        if not isinstance(entry, dict) or str(entry.get("type") or "").lower() != "oa":
            continue
        name = names.get(str(entry.get("channel_id") or "").strip())
        if name and entry.get("name") != name:
            entry["name"] = name
            changed += 1
    if changed:
        save_json(path, raw)
    return changed


def print_report(results):
    print("-" * 78)
    for data in results:
        if data.get("count"):
            state = "免登录" if data.get("anonymous") else "带 cookie"
        else:
            state = "抓取失败"
        head = "{:<8} {:<12} {:>3} 条  {}".format(
            data["channel_id"],
            (data.get("name") or "?")[:10],
            data.get("count") or 0,
            state)
        print(head)
        for item in data.get("newest") or []:
            print("           {} {} {}".format(item.get("date") or "----------",
                                               item.get("time") or "     ",
                                               (item.get("title") or "")[:44]))
        if data.get("error"):
            print("           ! {}".format(data["error"]))
    print("-" * 78)
    ok = [d for d in results if d.get("count")]
    if not ok:
        print("四个板块一条都没抓到。先确认这台机器在校内网：")
        print("    curl -I http://oa.jlu.edu.cn/        # 应当有响应而不是超时")
        print("开了 Clash / 全局代理时，把 10.100.18.18 或 oa.jlu.edu.cn 设为直连；")
        print("还是不行就用无痕窗口打开下面这个地址，看会不会跳登录页：")
        print("    {}".format(oa.list_url(results[0]["channel_id"] if results else "179577")))
        return 1
    print("抓到 {} 个板块。免登录即可抓：{}".format(
        len(ok), "是" if all(d.get("anonymous") for d in ok) else "否（需要 cookie_file）"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="摸 OA 四个板块的列表页（只读，不改任何东西）")
    ap.add_argument("--channels", default=",".join(cid for cid, _ in oa.BOARDS),
                    help="逗号分隔的 channelId，默认四个板块")
    ap.add_argument("--cookie-file", default="",
                    help="需要登录时：login_once.py 存下来的 cookie 文件")
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--save-fixtures", action="store_true",
                    help="把页面存到 tests/fixtures/oa_<channelId>.html")
    ap.add_argument("--write-sources", action="store_true",
                    help="把板块名写回 sources.json（只改 type=oa 的 name）")
    ap.add_argument("--json", action="store_true", help="打印可直接粘进 sources.json 的配置")
    args = ap.parse_args(argv)

    results = []
    for channel_id in parse_channels(args.channels):
        data = probe(channel_id, timeout=args.timeout, cookie_file=args.cookie_file)
        results.append(data)
        if args.save_fixtures and data.get("html"):
            path = save_fixture(channel_id, data["html"])
            print("已存页面 → {}".format(path))

    code = print_report(results)

    if args.write_sources:
        changed = write_sources(results)
        print("sources.json：改写 {} 个 OA 源的板块名".format(changed))

    if args.json:
        print(json.dumps(source_entries(results), ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

