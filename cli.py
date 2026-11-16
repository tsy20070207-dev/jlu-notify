#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jlu-notify 命令行。

常用：
    python3 cli.py test-push                 # 验证企微 / 微信能不能收到
    python3 cli.py run --dry-run             # 只抓不发，看看能抓到什么
    python3 cli.py run --once                # 抓一轮就退出（launchd/cron 用这个）
    python3 cli.py run --loop                # 常亮设备用：一直跑
    python3 cli.py list-sources              # 看每个源的游标和报错
    python3 cli.py stats                     # 看库里有多少条
    python3 cli.py digest --dry-run           # 预览今天所有通知的汇总（不发）
    python3 cli.py digest                     # 现在就发一份今天的汇总

抓取策略（一次 run 里同时实现 A/B 两级频率）：
    A 级源距上次抓取 >= level_a_minutes 才抓，B 级 >= level_b_minutes 才抓。
    所以 launchd 每 5 分钟调一次 --once，就等于 A 级 5 分钟、B 级 30 分钟。
"""

import argparse
import concurrent.futures as cf
import sys
import time

import notifier
import sources as source_registry
from common import FetchError, log, now_ts, ts_text
from config import enabled_sources, load_config, load_sources
from store import Store


def interval_seconds(source, cfg):
    """按源的 level 取轮询间隔（秒）。"""
    schedule = (cfg or {}).get("schedule") or {}
    level = str((source or {}).get("level") or "B").strip().upper()
    minutes = schedule.get("level_a_minutes") if level == "A" else schedule.get("level_b_minutes")
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        minutes = 5 if level == "A" else 30
    return max(1, minutes) * 60


def fetch_all(pending_sources, cfg):
    """并发抓取，返回 {源名: [条目] 或 FetchError}。DB 只在主线程写。"""
    request_cfg = (cfg or {}).get("request") or {}
    workers = max(1, int(request_cfg.get("workers") or 4))
    results = {}
    if not pending_sources:
        return results
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(source_registry.fetch, src, request_cfg): src
                   for src in pending_sources}
        for future in cf.as_completed(futures):
            src = futures[future]
            try:
                results[src["name"]] = future.result()
            except Exception as exc:
                results[src["name"]] = exc
    return results


def print_source_items(name, items, limit=3):
    log("{}: {} 条".format(name, len(items)))
    for item in items[:limit]:
        log("    {} {}".format(item.get("date") or "----------", (item.get("title") or "")[:52]), "DEBUG")


def cmd_run(args):
    cfg = load_config()
    all_sources = enabled_sources(load_sources())
    if args.source:
        wanted = set(args.source)
        all_sources = [s for s in all_sources if s["name"] in wanted]
    if not all_sources:
        log("没有可用的源：检查 sources.json 是否存在、enabled 是否为 true", "ERROR")
        return 2

    store = None if args.dry_run else Store(cfg["db_path"])
    try:
        due, skipped = [], 0
        for src in all_sources:
            if store is not None and not args.force and not store.is_due(src["name"], interval_seconds(src, cfg)):
                skipped += 1
                continue
            due.append(src)
        log("源 {} 个 / 本轮到期 {} 个 / 未到期 {} 个".format(len(all_sources), len(due), skipped))
        if not due:
            log("没有到期的源，本轮什么都不做")
            maybe_digest(cfg, store)
            return 0

        results = fetch_all(due, cfg)

        fresh, errors, baseline_sources = [], [], []
        for src in due:
            name = src["name"]
            outcome = results.get(name)
            if isinstance(outcome, Exception):
                errors.append((name, str(outcome)))
                log("{} 抓取失败: {}".format(name, outcome), "ERROR")
                if store is not None:
                    store.touch_source(name, ok=False, error=str(outcome))
                continue

            if args.dry_run:
                print_source_items(name, outcome)
                fresh.extend(dict(i, source=name) for i in outcome[:3])
                if store is not None:
                    store.touch_source(name, ok=True, count=len(outcome))
                continue

            first_time = (not store.baseline_done(name)
                          and (cfg.get("push") or {}).get("first_run_baseline_only", True)
                          and not args.no_baseline)
            if first_time:
                store.add_items(name, outcome, baseline=True)
                store.set_baseline(name)
                baseline_sources.append((name, len(outcome)))
                log("{} 首次抓取，建基线 {} 条（不推送）".format(name, len(outcome)))
            else:
                new_items = store.add_items(name, outcome, baseline=False)
                fresh.extend(new_items)
                if new_items:
                    log("{} 新增 {} 条".format(name, len(new_items)), "NEW")
            store.touch_source(name, ok=True, count=len(outcome))

        if args.dry_run:
            return finish_dry_run(cfg, fresh, errors, baseline_sources)

        code = finish_and_push(args, cfg, store, fresh, errors, baseline_sources)
        maybe_digest(cfg, store)      # 到了 digest.at 就顺手发当日汇总
        return code
    finally:
        if store is not None:
            store.close()


def finish_dry_run(cfg, fresh, errors, baseline_sources):
    log("=" * 62)
    text, used, _summary = notifier.build_message(fresh, cfg)
    if used:
        log("dry-run 预览（不会发送、不写库）：")
        print("-" * 62)
        print(text)
        print("-" * 62)
    else:
        log("dry-run：没有可预览的新条目")
    for name, count in baseline_sources:
        log("{}：正式运行时会建基线 {} 条（不推送）".format(name, count))
    if errors:
        log("{} 个源抓取失败（见上方 ERROR）".format(len(errors)), "WARN")
    return 0


def finish_and_push(args, cfg, store, fresh, errors, baseline_sources):
    max_per_run = int((cfg.get("push") or {}).get("max_per_run") or 60)
    pending = store.pending(limit=max_per_run)
    if not pending:
        log("本轮没有需要推送的新通知")
        return report(errors, ok=True, limit=source_error_tolerance(cfg))

    quiet = (cfg.get("schedule") or {}).get("quiet_hours")
    if notifier.in_quiet_hours(quiet) and not args.no_quiet:
        log("免打扰时段 {}，{} 条留在队列，时段结束后合并成一条推送".format(quiet, len(pending)), "SKIP")
        return report(errors, ok=True, limit=source_error_tolerance(cfg))

    text, used, summary = notifier.build_message(pending, cfg)
    if not used:
        log("拼装消息失败：没有可用条目", "ERROR")
        return 2
    log("准备推送 {} 条（队列共 {} 条，装不下的下轮继续）".format(len(used), len(pending)))

    results = notifier.Notifier(cfg).send(text, summary, dry_run=False, only=args.only)
    any_ok = notifier.log_results(results)
    if any_ok:
        marked = store.mark_pushed([item["id"] for item in used])
        log("已标记 {} 条为已推送".format(marked), "PUSH")
    elif all(r.get("skipped") for r in results):
        log("没有启用任何通道：先填 config.json 的 webhook / app_token，"
            "再用 python3 cli.py test-push 验证", "WARN")
    else:
        log("所有通道都失败了，条目留在队列里下轮重试", "WARN")
    return report(errors, ok=any_ok, limit=source_error_tolerance(cfg))


def source_error_tolerance(cfg):
    """单轮允许多少个源抓取失败而不算整轮失败（0 = 一个都不许）。"""
    raw = ((cfg or {}).get("run") or {}).get("max_source_errors", 3)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 3


def report(errors, ok=True, limit=0):
    if errors:
        log("{} 个源抓取失败：{}".format(
            len(errors), "、".join(name for name, _ in errors[:8])), "WARN")
        if len(errors) > limit:              # 超过容忍数：真故障，照旧标红
            return 1
        log("失败 {} 个 ≤ 容忍上限 {}，本轮不算运行错误".format(len(errors), limit), "WARN")
    return 0 if ok else 1


def cmd_test_push(args):
    cfg = load_config()
    only = getattr(args, "only", None)
    active = notifier.channels.active(cfg.get("channels") or {})
    if only:
        active = [name for name in active if name in set(only)]
    log("启用通道：{}".format("、".join(active) or "（无，请检查 config.json）"))
    if not active:
        return 1
    text = args.text or ("**吉大通知 · 通道自测**\n\n"
                         "看到这条就说明推送链路是通的。\n\n"
                         "时间：{}".format(ts_text()))
    results = notifier.Notifier(cfg).send(text, "吉大通知 · 通道自测",
                                          dry_run=args.dry_run, only=only)
    delivered = notifier.log_results(results)
    if args.dry_run:
        log("dry-run 完成：通道配置已读到，但没有真正发送", "SKIP")
        return 0
    if delivered:
        log("推送链路 OK", "PUSH")
        return 0
    if results and all(r.get("skipped") for r in results):
        log("一个通道都还没配好：打开 config.json，填 channels.wxpusher.spt"
            "（扫码拿 SPT_ 令牌，见 README 第 1 步）或 channels.wecom.webhook"
            "（占位符不算）", "ERROR")
        return 1
    log("没有通道成功：检查 webhook 是否过期、spt / app_token / uids 是否填全", "ERROR")
    return 1

def cmd_list_sources(args):
    cfg = load_config()
    store = Store(cfg["db_path"])
    try:
        rows = {r["name"]: r for r in store.source_rows()}
        srcs = load_sources()
        for src in srcs:
            st = rows.get(src["name"]) or {}
            flag = "" if src.get("enabled", True) else " [停用]"
            error = st.get("last_error") or ""
            print("{:<2} {:<22}{} 上次={} 条数={}{}".format(
                str(src.get("level", "B")).upper(),
                src["name"][:20],
                flag,
                st.get("last_fetch_text", "从未"),
                st.get("last_count", 0),
                "  错误=" + error[:36] if error else ""))
        log("共 {} 个源（A 级 {} 分钟 / B 级 {} 分钟）".format(
            len(srcs),
            (cfg.get("schedule") or {}).get("level_a_minutes"),
            (cfg.get("schedule") or {}).get("level_b_minutes")))
    finally:
        store.close()
    return 0


def cmd_stats(args):
    cfg = load_config()
    store = Store(cfg["db_path"])
    try:
        for key, value in store.stats().items():
            print("{:<10} {}".format(key, value))
    finally:
        store.close()
    return 0


# ---------------------------------------------------------------- 每日汇总

def digest_window(day=None):
    """返回 (日期标签, 当天 00:00 的 ts, 次日 00:00 的 ts)；day 形如 2026-11-16。"""
    try:
        tm = time.strptime(day, "%Y-%m-%d") if day else time.localtime()
    except (TypeError, ValueError):
        tm = time.localtime()
    start = time.mktime((tm.tm_year, tm.tm_mon, tm.tm_mday, 0, 0, 0, 0, 0, -1))
    return "{}月{}日".format(tm.tm_mon, tm.tm_mday), start, start + 86400


def build_digest_for(cfg, store, day=None):
    """查某天的通知并拼成汇总，返回 (text, summary, 总条数)。"""
    digest_cfg = cfg.get("digest") or {}
    label, start, end = digest_window(day)
    limit = max(1, int(digest_cfg.get("max_items") or 40))
    # 多取一条：用来判断「今天其实还有更多条」，好在消息里把总数说清楚
    items = store.items_between(start, min(end, now_ts() + 1), limit=limit + 1)
    extra = max(0, len(items) - limit)
    budget = int(digest_cfg.get("max_chars") or 1700)
    cap = notifier.channel_char_cap(cfg)
    if cap:
        budget = min(budget, cap)
    return notifier.build_digest(items[:limit], cfg, day_label=label,
                                 budget=budget, extra=extra)


def maybe_digest(cfg, store):
    """到了 digest.at 且今天还没发过 → 发当日汇总；返回是否真的发了。

    只有至少一个通道成功才写标记，所以发失败时下一轮（5 分钟后）会自动重试。
    """
    digest_cfg = cfg.get("digest") or {}
    if store is None or not digest_cfg.get("enabled"):
        return False
    at = notifier.parse_hhmm(digest_cfg.get("at") or "22:30", 22 * 60 + 30)
    tm = time.localtime()
    if tm.tm_hour * 60 + tm.tm_min < at:
        return False
    today = time.strftime("%Y-%m-%d")
    if store.get_meta("digest_last_date") == today:
        return False
    quiet = (cfg.get("schedule") or {}).get("quiet_hours")
    if notifier.in_quiet_hours(quiet):
        log("汇总时间 {} 落在免打扰时段 {} 内，跳过：把 digest.at 改早一点".format(
            digest_cfg.get("at"), quiet), "WARN")
        return False

    text, summary, count = build_digest_for(cfg, store, None)
    if not text:
        log("今天还没有新通知，本轮不发汇总", "SKIP")
        return False
    results = notifier.Notifier(cfg).send(text, summary)
    if notifier.log_results(results):
        store.set_meta("digest_last_date", today)
        log("每日汇总已发出：{} 条 / {} 字符".format(count, len(text)), "PUSH")
        return True
    log("每日汇总没发出去，下一轮自动重试", "WARN")
    return False


def cmd_digest(args):
    """手动发一份汇总（不受 digest.at 限制，用来预览或补发）。"""
    cfg = load_config()
    store = Store(cfg["db_path"])
    try:
        text, summary, count = build_digest_for(cfg, store, args.date)
        if not text:
            log("{} 没有通知，汇总为空".format(args.date or "今天"), "SKIP")
            return 0
        log("汇总 {} 条 / {} 字符".format(count, len(text)))
        if args.dry_run:
            print("-" * 62)
            print(text)
            print("-" * 62)
            log("dry-run：只预览，没有发送", "SKIP")
            return 0
        results = notifier.Notifier(cfg).send(text, summary, only=args.only)
        if notifier.log_results(results):
            store.set_meta("digest_last_date", args.date or time.strftime("%Y-%m-%d"))
            log("每日汇总已发出：{} 条".format(count), "PUSH")
            return 0
        log("汇总没发出去：检查通道配置", "ERROR")
        return 1
    finally:
        store.close()


def build_parser():
    parser = argparse.ArgumentParser(
        description="吉林大学校内通知 → 企业微信 / 微信 推送")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="抓取并推送")
    run.add_argument("--once", action="store_true", help="抓一轮就退出（launchd/cron 用）")
    run.add_argument("--loop", action="store_true", help="常亮设备：循环运行")
    run.add_argument("--dry-run", action="store_true", help="只抓+预览，不写库不推送")
    run.add_argument("--source", action="append", help="只跑指定源（可重复）")
    run.add_argument("--only", action="append", help="只走指定通道：wecom / wxpusher")
    run.add_argument("--force", action="store_true", help="忽略频率限制，强制抓所有源")
    run.add_argument("--no-baseline", action="store_true", help="不建基线，现有条目也推一遍")
    run.add_argument("--no-quiet", action="store_true", help="忽略免打扰时段")
    run.set_defaults(func=cmd_run)

    test = sub.add_parser("test-push", help="发一条测试消息验证通道")
    test.add_argument("--text", help="自定义测试内容")
    test.add_argument("--only", action="append", help="只测指定通道：wecom / wxpusher")
    test.add_argument("--dry-run", action="store_true", help="只打印不发送")
    test.set_defaults(func=cmd_test_push)

    listing = sub.add_parser("list-sources", help="查看每个源的游标与报错")
    listing.set_defaults(func=cmd_list_sources)

    stats = sub.add_parser("stats", help="查看数据库统计")
    stats.set_defaults(func=cmd_stats)

    dig = sub.add_parser("digest", help="发一份每日汇总（默认今天）")
    dig.add_argument("--date", help="指定日期 YYYY-MM-DD，默认今天")
    dig.add_argument("--dry-run", action="store_true", help="只预览，不发送")
    dig.add_argument("--only", action="append", help="只走指定通道：wecom / wxpusher")
    dig.set_defaults(func=cmd_digest)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1

    if getattr(args, "loop", False):
        cfg = load_config()
        sleep_s = max(60, int((cfg.get("schedule") or {}).get("level_a_minutes") or 5) * 60)
        log("常亮模式：每 {} 秒一轮（Ctrl+C 退出）".format(sleep_s))
        while True:
            try:
                args.func(args)
            except KeyboardInterrupt:
                log("收到 Ctrl+C，退出")
                break
            except Exception as exc:
                log("本轮异常: {}".format(exc), "ERROR")
            try:
                time.sleep(sleep_s)
            except KeyboardInterrupt:
                break
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

