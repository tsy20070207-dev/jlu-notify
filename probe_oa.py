#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在校内网跑一次：打开 OA → 你手动登录 → 把通知列表页结构 dump 下来。

为什么必须手动登录：
    OA 走统一身份认证，可能还有验证码。自动登录既不稳也不合规，
    所以本脚本只做一件事：开一个有头浏览器，你自己登进去，然后按回车，
    它把当前页面的 HTML 存成 data/oa_dump.html，并打印结构摘要。

用法（必须连着校园网，或在校内的常亮设备上）：
    pip3 install playwright && python3 -m playwright install chromium
    python3 probe_oa.py
    python3 probe_oa.py --url "http://oa.jlu.edu.cn/具体通知列表页"

跑完把终端打印的摘要发出来，就能按真实 DOM 定 sources/oa.py 的解析规则。
"""

import argparse
import os
import re

import parser
from common import BASE_DIR, log, pj

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    print("缺少 Playwright，请先执行：\n  pip3 install playwright\n  python3 -m playwright install chromium")
    raise SystemExit(1)

DEFAULT_URL = "http://oa.jlu.edu.cn/"


def summarize(html, url):
    """打印结构摘要：有多少个 li / tr、前 10 个链接长什么样、能解析出几条。"""
    print("-" * 62)
    print("页面标题 : {}".format(parser.page_title(html) or "(无)"))
    print("HTML 长度: {} 字符".format(len(html)))
    print("li 块    : {} 个".format(len(re.findall(r"<li\b", html, re.I))))
    print("tr 块    : {} 个".format(len(re.findall(r"<tr\b", html, re.I))))
    print("a 标签   : {} 个".format(len(re.findall(r"<a\b", html, re.I))))
    print("含 2026/2025 的日期: {} 处".format(
        len(re.findall(r"20\d{2}\s*[-/.年]", html))))

    items = parser.parse_list(html, base_url=url)
    print("按博达规则能解析出: {} 条".format(len(items)))
    if items:
        for item in items[:10]:
            print("  {}  {}".format(item["date"], item["title"][:40]))
    else:
        print("  → 说明 OA 不是博达结构，需要按下面的链接样本改解析规则")

    print("-" * 62)
    print("前 12 个链接样本（href | 文字 | 是否有日期）：")
    shown = 0
    for match in re.finditer(r'<a\b([^>]*)>(.*?)</a>', html, re.S | re.I):
        attrs, inner = match.group(1), parser.clean_text(match.group(2))
        href_m = re.search(r'href\s*=\s*["\']([^"\']+)["\']', attrs, re.I)
        if not href_m or not inner:
            continue
        window = html[match.start():match.start() + 300]
        has_date = bool(re.search(r"\d{1,2}\s*[-/.]\s*\d{1,2}|20\d{2}", window))
        print("  {:<46} | {:<26} | {}".format(href_m.group(1)[:46], inner[:26], "有" if has_date else "-"))
        shown += 1
        if shown >= 12:
            break
    print("-" * 62)


def main():
    ap = argparse.ArgumentParser(description="校内网探测 OA 通知列表页结构")
    ap.add_argument("--url", default=DEFAULT_URL, help="要打开的地址（默认 OA 首页）")
    ap.add_argument("--out", default=None, help="dump 保存路径（默认 data/oa_dump.html）")
    args = ap.parse_args()

    out = args.out or pj("data", "oa_dump.html")
    profile = os.path.join(BASE_DIR, "browser_profile_oa")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            profile, headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        log("打开 {}".format(args.url))
        try:
            page.goto(args.url, timeout=60000)
        except Exception as exc:
            log("打开失败：{}（确定现在连着校园网吗？）".format(exc), "ERROR")
            context.close()
            return 1
        print()
        print("=" * 62)
        print("请在弹出的浏览器里手动登录 OA，并点进「通知公告」列表页。")
        print("列表页显示出来后，回到终端按回车，我来 dump 结构。")
        print("=" * 62)
        input("准备就绪后按回车 > ")
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        html = page.content()
        url = page.url
        context.close()

    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    log("已保存到 {}（{} 字符）".format(out, len(html)))
    summarize(html, url)
    print("把这个文件的路径和上面的摘要发给开发者，就能定 OA 的解析规则了。")
    print("之后把 sources.json 里 OA 那条的 enabled 改成 true 即可。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
