#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校园网侧守护：连不上 OA 就静默退出，连得上就跑一轮（只跑 OA 的 4 个板块）。

装在能连校园网的机器上（宿舍小主机 / 机房 / 常开的笔记本），launchd 或计划任务
每 15 分钟喊一次：

    python3 oa_watch.py              # 正常：探测 → 抓 4 个板块 → 推送
    python3 oa_watch.py --dry-run    # 只抓不推，看抓到什么
    python3 oa_watch.py --status     # 只探测，确认这台机器在校内网

为什么单独一个脚本：GitHub Actions 跑在公网，永远连不上 10.100.18.18。云端负责
公网源兜底，这台机器只负责 OA。连不上时直接 exit 0 —— 出门用 4G 时 launchd 还在
每 15 分钟喊一次，不该为此把日志刷红、更不该发告警。
"""

import argparse
import os
import re
import socket
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import oa_check                                        # noqa: E402
from common import log                                 # noqa: E402
from config import enabled_sources, load_sources       # noqa: E402

OA_HOST = "10.100.18.18"          # oa.jlu.edu.cn 的内网地址
OA_PORT = 80
PLACEHOLDER_RE = re.compile(r"^OA[·\-]?\d+$")


def reachable(host=OA_HOST, port=OA_PORT, timeout=2.0):
    """TCP 探测：比 HTTP 快，也不会在 OA 上留下访问日志。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def oa_sources():
    return [s for s in enabled_sources(load_sources())
            if str(s.get("type") or "site").lower() == "oa"]


def needs_names(sources):
    """板块名还是占位符（OA·179578）时返回 True —— 首次部署会走到这里。"""
    return any(PLACEHOLDER_RE.match(str(s.get("name") or "")) for s in sources)


def fix_names(sources, timeout=15):
    """用页面里的 channelName 把占位符改成真名（改完要重新读 sources.json）。"""
    results = []
    for src in sources:
        channel_id = str(src.get("channel_id") or "").strip()
        if not channel_id:
            continue
        data = oa_check.probe(channel_id, timeout=timeout)
        if data.get("count"):
            results.append(data)
        else:
            log("{} 板块名探测失败：{}".format(channel_id, data.get("error") or "未知"), "WARN")
    if not results:
        return 0
    changed = oa_check.write_sources(results)
    if changed:
        log("已按页面 channelName 改正 {} 个 OA 源的板块名".format(changed), "NEW")
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser(description="校园网侧：能连 OA 就跑一轮，连不上静默退出")
    ap.add_argument("--dry-run", action="store_true", help="只抓不推")
    ap.add_argument("--status", action="store_true", help="只探测网络与板块，不抓不推")
    ap.add_argument("--timeout", type=float, default=2.0, help="TCP 探测超时（秒）")
    ap.add_argument("--no-fix-names", action="store_true", help="不去纠正占位符板块名")
    args = ap.parse_args(argv)

    if not reachable(timeout=args.timeout):
        log("不在校园网（连不上 {}:{}），本轮跳过".format(OA_HOST, OA_PORT), "SKIP")
        return 0

    sources = oa_sources()
    if not sources:
        log("sources.json 里没有启用的 OA 源（type=oa），先跑 python3 oa_check.py", "ERROR")
        return 2

    if args.status:
        for src in sources:
            log("{}  channel_id={}  pages={}".format(
                src.get("name"), src.get("channel_id") or "-", src.get("pages") or 1))
        log("{} 可以连上，OA 源 {} 个".format(OA_HOST, len(sources)))
        return 0

    if not args.no_fix_names and needs_names(sources):
        fix_names(sources)
        sources = oa_sources()             # 名字可能刚被改正，重新读一遍

    run_argv = ["run", "--once", "--force"]
    if args.dry_run:
        run_argv.append("--dry-run")
    for src in sources:
        run_argv.extend(["--source", src["name"]])

    import cli                             # 放在这里：--status 时不必加载整条推送链
    return cli.main(run_argv)


if __name__ == "__main__":
    raise SystemExit(main())

