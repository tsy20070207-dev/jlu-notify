#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""需要登录的源：手动登录一次 → 保存 cookie → 后续复用。

公网可直连的 108 个单位站点都不需要登录，这个脚本只给两类场景准备：
  * 需要统一身份认证才看得到的栏目（个别内网系统）；
  * 以后想顺手把教务 / 研究生系统里的通知也接进来。

红线：不写死学号密码、不自动提交登录表单、不绕过验证码。
     只复用你自己手动登录后的会话，且只做低频只读轮询。

用法：
    python3 login_once.py --url https://某站点/栏目页
    python3 login_once.py --url ... --check     # 只验证已保存的会话还能不能用

产物 data/session.json（已在 .gitignore 里，不会外传）。
"""

import argparse
import json
import os
import time

from common import BASE_DIR, log, load_json, pj, save_json

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    print("缺少 Playwright，请先执行：\n  pip3 install playwright\n  python3 -m playwright install chromium")
    raise SystemExit(1)

SESSION_PATH = pj("data", "session.json")
PROFILE_DIR = os.path.join(BASE_DIR, "browser_profile_login")


def cookie_header(cookies):
    return "; ".join("{}={}".format(c.get("name"), c.get("value")) for c in cookies or [])


def save_session(url, context):
    cookies = []
    try:
        cookies = context.cookies()
    except Exception:
        pass
    data = {
        "url": url,
        "captured_at": int(time.time()),
        "cookies": cookies,
        "cookie_header": cookie_header(cookies),
    }
    save_json(SESSION_PATH, data)
    return data


def capture(url):
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            PROFILE_DIR, headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        log("打开 {}".format(url))
        page.goto(url, timeout=60000)
        print()
        print("=" * 62)
        print("请在浏览器里手动登录，确认能看到通知列表页，然后回到终端按回车。")
        print("=" * 62)
        input("按回车保存会话 > ")
        data = save_session(page.url, context)
        context.close()

    log("已保存 {} 条 cookie → {}".format(len(data["cookies"]), SESSION_PATH))
    log("会话页面: {}".format(data["url"]))
    return data


def check():
    data = load_json(SESSION_PATH)
    if not data:
        log("没有已保存的会话，先跑一次 python3 login_once.py --url ...", "ERROR")
        return 1
    age_min = (time.time() - int(data.get("captured_at") or 0)) / 60.0
    log("会话采样于 {:.0f} 分钟前，共 {} 条 cookie".format(age_min, len(data.get("cookies") or [])))
    print("Cookie 头（可直接粘到需要鉴权的请求里）:")
    print(data.get("cookie_header") or "(空)")
    if age_min > 60 * 12:
        log("超过 12 小时了，多半已过期，建议重新登录一次", "WARN")
    return 0


def main():
    ap = argparse.ArgumentParser(description="手动登录一次并保存会话")
    ap.add_argument("--url", help="登录后要停留的页面地址")
    ap.add_argument("--check", action="store_true", help="只查看已保存的会话")
    args = ap.parse_args()

    if args.check:
        return check()
    if not args.url:
        ap.error("需要 --url（或加 --check 查看已有会话）")
    data = capture(args.url)
    print()
    print("Cookie 长度 {} 字符。".format(len(data.get("cookie_header") or "")))
    print("现在可以把它交给 sources.json 里对应源的 headers.Cookie 字段（需自行扩展 sources/site.py）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
