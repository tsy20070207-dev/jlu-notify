# -*- coding: utf-8 -*-
"""冒烟测试：只用本地真实夹具，不联网、不需要登录。

覆盖点：
  1. 三种真实列表页 DOM（日期在后 / 日期在前 / day+year 拆分）都解析得对
  2. 相对链接补成绝对地址；无日期的导航链接被过滤掉
  3. 化学学院页面正文里的「公示时间」「报名时间」不会被当成发布日期
  4. 详情页头部「时间：2026-10-13」抽取
  5. 表格布局兜底
  6. SQLite：URL 级去重（跨源也不重复）、基线不推送、pending / mark_pushed / is_due
  7. 消息拼装：条数上限、溢出提示、markdown 链接、摘要
  8. 免打扰时段（含跨零点）
  9. UTF-8 字节截断不切碎中文
 10. WxPusher 通道：SPT 优先于 appToken、sptList、错误码映射（不联网）

运行: python3 tests/smoke_test.py
"""

import json
import os
import sys
import tempfile
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import notifier      # noqa: E402
import parser        # noqa: E402
import discover      # noqa: E402
import cli           # noqa: E402
from common import truncate_bytes, truncate_chars      # noqa: E402
from config import load_sources                        # noqa: E402
from store import Store                                # noqa: E402

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
RESULTS = []


def check(name, cond, extra=""):
    ok = bool(cond)
    RESULTS.append(ok)
    print("{}  {}{}".format("PASS" if ok else "FAIL", name,
                            "  -> {}".format(extra) if extra else ""))
    return ok


def fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


# ---------------------------------------------------------------- 列表解析

def test_school_list():
    """学校主页：日期在链接之后，且部分标题被站点截断成 ..."""
    items = parser.parse_list(fixture("school.html"), "https://www.jlu.edu.cn/index/tzgg.htm")
    check("学校通知：解析出 20 条", len(items) == 20, "实际 {}".format(len(items)))
    first = items[0]
    check("学校通知：日期在后也取对", first["date"] == "2026-10-13", first["date"])
    check("学校通知：相对链接补成绝对地址",
          first["url"] == "https://www.jlu.edu.cn/info/1095/58371.htm", first["url"])
    check("学校通知：标题无残留标签",
          "<" not in first["title"] and "href" not in first["title"])
    check("学校通知：每条都带日期", all(i["date"] for i in items))


def test_jwc_list():
    """本科生院：日期在链接之前（span 在前）"""
    items = parser.parse_list(fixture("jwc.html"), "https://jwc.jlu.edu.cn/zxzx/tzgg.htm")
    check("本科生院：解析出 15 条", len(items) == 15, "实际 {}".format(len(items)))
    first = items[0]
    check("本科生院：日期在前也取对", first["date"] == "2026-10-20", first["date"])
    check("本科生院：标题正确", first["title"] == "关于万相成等101名同学拟调整专业的公示",
          first["title"])
    check("本科生院：链接绝对化",
          first["url"] == "https://jwc.jlu.edu.cn/info/1050/12735.htm", first["url"])


def test_chem_list():
    """化学学院：日期拆成 class=day(11-11) + class=year(2026) 两个标签"""
    items = parser.parse_list(fixture("chem.html"), "https://chem.jlu.edu.cn/xwtz/tzgg.htm")
    check("化学学院：解析出 15 条", len(items) == 15, "实际 {}".format(len(items)))
    check("化学学院：day+year 拼成完整日期", items[0]["date"] == "2026-11-11", items[0]["date"])
    check("化学学院：摘要正文没被当成标题",
          all("公示时间" not in i["title"] for i in items))
    check("化学学院：日期都落在今年",
          all(i["date"].startswith("2026-") for i in items))


def test_xsc_list():
    """学工部：日期是「日 + 年月」两段、整块包在 <a> 里、标题只能靠 title 属性"""
    items = parser.parse_list(fixture("xsc.html"), "https://xsc.jlu.edu.cn/index/tzgg.htm")
    check("学工部：解析出 10 条", len(items) == 10, "实际 {}".format(len(items)))
    first = items[0]
    check("学工部：日-年月拆开也能拼成日期", first["date"] == "2026-11-16", first["date"])
    check("学工部：标题取自 <a title>，没混进摘要",
          first["title"].endswith("的通知") and len(first["title"]) < 70,
          first["title"][:40])
    check("学工部：标题里没有日期残留", "2026-11" not in first["title"])


def test_phy_list():
    """物理学院：日期是 <b>日</b><span> / 年月</span>，标题在块内 <h3> 里"""
    items = parser.parse_list(fixture("phy.html"), "https://phy.jlu.edu.cn/xzgz/tzgg.htm")
    check("物理学院：解析出 10 条", len(items) == 10, "实际 {}".format(len(items)))
    first = items[0]
    check("物理学院：日 / 年月 也能拼成日期", first["date"] == "2026-11-12", first["date"])
    check("物理学院：标题取自 <h3>",
          first["title"] == "关于申报物理学院有组织科研专项的通知", first["title"])
    check("物理学院：标题里没有日期残留",
          all("2026-11" not in i["title"] and "/" not in i["title"][:3] for i in items))
    check("物理学院：链接绝对化",
          first["url"] == "https://phy.jlu.edu.cn/info/1052/7388.htm", first["url"])


TRAP_HTML = """
<ul class="list-text">
  <li>
    <div class="date"><p class="day">03-05</p><p class="year">2025</p></div>
    <div class="text">
      <a href="../info/1008/1.htm" class="tit">关于开展实验室安全检查工作的通知 </a>
      <div class="abst">报名时间：2026年11月11日至11月13日，逾期不再受理。</div>
    </div>
  </li>
</ul>
"""


def test_date_trap():
    """日期栏 2025-03-05 与正文「报名时间 2026-11-11」冲突时必须取日期栏"""
    items = parser.parse_list(TRAP_HTML, "https://chem.jlu.edu.cn/xwtz/tzgg.htm")
    check("日期陷阱：解析出 1 条", len(items) == 1, "实际 {}".format(len(items)))
    check("日期陷阱：取排版日期而不是正文日期",
          items[0]["date"] == "2025-03-05", items[0]["date"])


NAV_HTML = """
<ul class="nav">
  <li><a href="../index.htm">首页</a></li>
  <li><a href="../xwtz/tzgg.htm">通知公告</a></li>
  <li><a href="../info/1/9.htm">真的通知标题在这里</a><span>2026-09-02</span></li>
</ul>
"""


def test_nav_filter():
    """没有日期的导航链接必须被丢掉，不能当成通知"""
    items = parser.parse_list(NAV_HTML, "https://a.jlu.edu.cn/xwtz/tzgg.htm")
    check("导航过滤：只剩真正带日期的 1 条", len(items) == 1, "实际 {}".format(len(items)))
    check("导航过滤：留下的是通知", items[0]["title"].startswith("真的通知"), items[0]["title"])


TABLE_HTML = """
<table>
  <tr><td><a href="/info/2001/5.htm">表格布局的通知条目</a></td><td>2026-09-01</td></tr>
  <tr><td><a href="/info/2001/6.htm">表格布局的第二条</a></td><td>2026/9/3</td></tr>
</table>
"""


def test_table_layout():
    items = parser.parse_list(TABLE_HTML, "https://b.jlu.edu.cn/xwtz/tzgg.htm")
    check("表格布局：解析出 2 条", len(items) == 2, "实际 {}".format(len(items)))
    check("表格布局：斜杠日期归一化", items[1]["date"] == "2026-09-03", items[1]["date"])


def test_detail_time():
    check("详情页：时间：2026-10-13",
          parser.parse_detail_time(fixture("detail.html")) == "2026-10-13",
          parser.parse_detail_time(fixture("detail.html")))
    check("详情页：中文日期格式",
          parser.parse_detail_time("<p>发布时间：2026年1月8日</p>") == "2026-01-08")
    check("详情页：没有时间时返回空串",
          parser.parse_detail_time("<p>没有任何日期</p>") == "")

# ---------------------------------------------------------------- 存储

def test_store():
    tmp = tempfile.mkdtemp(prefix="jlu-notify-test-")
    store = Store(os.path.join(tmp, "test.db"))
    try:
        batch = [
            {"title": "通知A", "url": "https://x.jlu.edu.cn/info/1/1.htm", "date": "2026-09-01"},
            {"title": "通知B", "url": "https://x.jlu.edu.cn/info/1/2.htm", "date": "2026-09-02"},
        ]
        fresh = store.add_items("源A", batch)
        check("存储：新条目全部入库", len(fresh) == 2, "实际 {}".format(len(fresh)))
        check("存储：重复抓同一页不再产生新条目", store.add_items("源A", batch) == [])
        check("存储：不同源抓到同一 URL 也不重复推（转载去重）",
              store.add_items("源B", batch[:1]) == [])
        check("存储：待推送 2 条", store.pending_count() == 2, store.pending_count())

        marked = store.mark_pushed([item["id"] for item in fresh])
        check("存储：标记已推送 2 条", marked == 2, marked)
        check("存储：标记后队列清空", store.pending_count() == 0)

        history = [{"title": "历史通知", "url": "https://y.jlu.edu.cn/info/1/9.htm", "date": "2026-01-01"}]
        check("存储：基线模式不产生待推送",
              store.add_items("源C", history, baseline=True) == [] and store.pending_count() == 0)

        store.touch_source("源A", ok=True, count=2)
        check("存储：刚抓过的源 5 分钟内不到期", store.is_due("源A", 300) is False)
        check("存储：没抓过的源立刻到期", store.is_due("源Z", 300) is True)
        store.touch_source("源A", ok=False, error="HTTP 500")
        check("存储：失败会记到 last_error",
              "HTTP 500" in (store.source_state("源A") or {}).get("last_error", ""))
        check("存储：统计里只统计抓过的源",
              store.stats()["sources"] == 1, store.stats())
    finally:
        store.close()


# ---------------------------------------------------------------- 消息拼装

PUSH_CFG = {"push": {"max_items": 3, "max_chars": 1800, "title_prefix": "吉大通知"}}


def test_message():
    items = [{"title": "标题{}".format(i), "url": "https://a.jlu.edu.cn/{}.htm".format(i),
              "source": "测试源", "date": "2026-09-0{}".format(i)} for i in range(1, 6)]
    text, used, summary = notifier.build_message(items, PUSH_CFG)
    check("拼装：条数受 max_items 限制", len(used) == 3, len(used))
    check("拼装：溢出条目有提示", "还有 2 条" in text, text[-40:].replace("\n", " "))
    check("拼装：摘要带条数", summary == "吉大通知 · 3 条新通知", summary)
    check("拼装：markdown 链接格式正确",
          "**1. 标题1**\n[测试源 · 2026-09-01](https://a.jlu.edu.cn/1.htm)" in text)
    check("拼装：空列表返回空", notifier.build_message([], PUSH_CFG) == ("", [], "吉大通知"))

    tight = {"push": {"max_items": 8, "max_chars": 220, "title_prefix": "吉大通知"}}
    _text, used_tight, _summary = notifier.build_message(items, tight)
    check("拼装：字符预算不够时少放几条而不是截断", 0 < len(used_tight) < 5, len(used_tight))


def ts_at(hhmm):
    now = time.localtime()
    hour, minute = [int(x) for x in hhmm.split(":")]
    return time.mktime((now.tm_year, now.tm_mon, now.tm_mday, hour, minute, 0, 0, 0, -1))


def test_digest():
    """每日汇总：口径（当天、不含基线）、同日去重、超长降级。

    注意：这里不测 maybe_digest 真正「发」的路径，只测「已发过 / 已关闭」这两个
    一定不会联网的分支，避免跑测试时给手机发消息。
    """
    tmp = tempfile.mkdtemp(prefix="jlu-digest-")
    store = Store(os.path.join(tmp, "d.db"))
    try:
        today = time.strftime("%Y-%m-%d")
        check("汇总：meta 默认为空", store.get_meta("digest_last_date") == "")
        store.set_meta("digest_last_date", today)
        check("汇总：meta 能读回", store.get_meta("digest_last_date") == today)

        label, start, end = cli.digest_window(today)
        check("汇总：时间窗正好一天", end - start == 86400, end - start)

        store.add_items("源A", [{"title": "今日通知", "url": "https://a.jlu.edu.cn/1.htm",
                                 "date": today}])
        store.add_items("源B", [{"title": "历史通知", "url": "https://b.jlu.edu.cn/9.htm",
                                 "date": "2026-01-01"}], baseline=True)
        items = store.items_between(start, end)
        check("汇总：只算当天、且基线条目不计入",
              [i["title"] for i in items] == ["今日通知"], [i["title"] for i in items])

        cfg = {"push": {"title_prefix": "吉大通知"},
               "digest": {"max_items": 40, "max_chars": 1700}}
        text, summary, used = notifier.build_digest(items, cfg, day_label=label)
        check("汇总：标题带日期和条数", "汇总（1 条）" in text and label in text, text[:40])
        check("汇总：覆盖条数正确", used == 1, used)
        check("汇总：摘要带前缀", summary.startswith("吉大通知"), summary)
        check("汇总：没有条目时返回空消息", notifier.build_digest([], cfg)[0] == "")

        many = [{"title": "标题{}".format(i), "url": "https://a.jlu.edu.cn/{}.htm".format(i),
                 "source": "测试源", "date": today} for i in range(30)]
        text, _, _ = notifier.build_digest(many, cfg, day_label=label)
        check("汇总：多了就降级成「仅标题」清单",
              "其余" in text and len(text) <= 1700, (len(text), text[:30]))
        tight = {"push": cfg["push"], "digest": {"max_items": 40, "max_chars": 700}}
        text, _, _ = notifier.build_digest(many, tight, day_label=label)
        check("汇总：预算被挤爆时也不超长，并说明还有多少条",
              len(text) <= 700 and "还有" in text, (len(text), text[-30:]))

        on = {"push": cfg["push"], "digest": {"enabled": True, "at": "00:00"}}
        check("汇总：同一天不会发第二遍（不联网）", cli.maybe_digest(on, store) is False)
        store.set_meta("digest_last_date", "")
        off = {"push": cfg["push"], "digest": {"enabled": False}}
        check("汇总：开关关掉就什么都不做", cli.maybe_digest(off, store) is False)
    finally:
        store.close()


def test_quiet_hours():
    night = ["23:30", "07:00"]
    check("免打扰：02:00 在 23:30-07:00 内", notifier.in_quiet_hours(night, ts_at("02:00")))
    check("免打扰：23:45 在时段内", notifier.in_quiet_hours(night, ts_at("23:45")))
    check("免打扰：06:59 仍在时段内", notifier.in_quiet_hours(night, ts_at("06:59")))
    check("免打扰：07:00 正好结束", notifier.in_quiet_hours(night, ts_at("07:00")) is False)
    check("免打扰：12:00 不受影响", notifier.in_quiet_hours(night, ts_at("12:00")) is False)
    check("免打扰：不跨零点的 01:00-05:00 在 02:00 生效",
          notifier.in_quiet_hours(["01:00", "05:00"], ts_at("02:00")))
    check("免打扰：不跨零点时段外放行",
          notifier.in_quiet_hours(["01:00", "05:00"], ts_at("12:00")) is False)
    check("免打扰：配置为空/非法时不静音",
          notifier.in_quiet_hours([], ts_at("02:00")) is False
          and notifier.in_quiet_hours(None, ts_at("02:00")) is False)


# ---------------------------------------------------------------- 其它

def test_truncate():
    text = "吉大通知" * 200
    cut = truncate_bytes(text, 60)
    check("截断：字节数不超上限", len(cut.encode("utf-8")) <= 60, len(cut.encode("utf-8")))
    check("截断：不产生乱码", "\ufffd" not in cut and cut.startswith("吉大通知"))
    check("截断：短文本原样返回", truncate_bytes("短", 60) == "短")
    check("截断：字符截断长度精确", len(truncate_chars(text, 10)) == 10)


def test_sources_config():
    tmp = tempfile.mkdtemp(prefix="jlu-src-")
    path = os.path.join(tmp, "sources.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"sources": [
            {"name": "手工源", "url": "https://c.jlu.edu.cn/xwtz/tzgg.htm"},
            {"url": "https://d.jlu.edu.cn/xwtz/tzgg.htm"},
            {"name": "缺 url 的脏数据"},
        ]}, f, ensure_ascii=False)
    srcs = load_sources(path)
    check("配置：没有 url 的条目被丢掉", len(srcs) == 2, len(srcs))
    check("配置：默认 level=B 且启用",
          srcs[0]["level"] == "B" and srcs[0]["enabled"] is True)
    check("配置：缺 name 时自动补", srcs[1]["name"] == "源2", srcs[1]["name"])


def test_unit_name():
    """发现器从首页 title 榨单位名：脏数据不能变成「（重要）吉林」"""
    cases = (
        ("吉林大学-汽车工程学院", "汽车工程学院"),
        ("（重要）吉林-文学院（国际语言学院）", "文学院（国际语言学院）"),
        ("首页-吉林大学材料科学与工程学院", "材料科学与工程学院"),
        ("吉林大学党委学生工作部（处）", "党委学生工作部（处）"),
        ("吉林大学法学院", "法学院"),
    )
    for raw, want in cases:
        got = discover.clean_unit_name(raw)
        check("单位名：{} → {}".format(raw[:12], want), got == want, got)
    check("单位名：只有「吉林大学」时返回空，交给域名兜底",
          discover.clean_unit_name("吉林大学-吉林大学") == "")
    check("单位名：空标题不崩", discover.clean_unit_name(None) == "")


def test_wxpusher_channel():
    """WxPusher 通道：SPT 优先级、请求格式、错误码映射（全程不联网）。"""
    from channels import wxpusher

    calls = []
    reply = [{"code": 1000, "msg": "处理成功"}]

    def fake_post(url, payload, timeout=15, retries=1):
        calls.append((url, payload))
        return 200, json.dumps(reply[0], ensure_ascii=False)

    real_post = wxpusher.http_post_json
    wxpusher.http_post_json = fake_post
    try:
        # 1) 只配 spt → 走 simple-push，用 spt 字段，markdown 正文
        res = wxpusher.send({"spt": "SPT_abc"}, "**正文**", "摘要")
        url, payload = calls[0]
        check("WxPusher：配了 spt 就走 simple-push 接口",
              url == wxpusher.SIMPLE_API, url)
        check("WxPusher：单个 SPT 用 spt 字段", payload.get("spt") == "SPT_abc")
        check("WxPusher：markdown 用 contentType=3", payload.get("contentType") == 3)
        check("WxPusher：summary 原样带上", payload.get("summary") == "摘要")
        check("WxPusher：1000 返回 ok", res["ok"] and not res["skipped"], res["detail"])

        # 2) spt + app_token 同时存在 → 仍走 SPT（不会重复发两遍）
        calls[:] = []
        wxpusher.send({"spt": "SPT_abc", "app_token": "AT_xyz",
                       "uids": ["UID_1"]}, "正文", "摘要")
        check("WxPusher：spt 与 app_token 同时配置时优先 SPT",
              calls[0][0] == wxpusher.SIMPLE_API and calls[0][1].get("spt") == "SPT_abc")

        # 3) 多个 SPT（中文逗号也认）→ sptList
        calls[:] = []
        wxpusher.send({"spt": "SPT_a，SPT_b"}, "正文", "摘要")
        check("WxPusher：多个 SPT 用 sptList 数组",
              calls[0][1].get("sptList") == ["SPT_a", "SPT_b"])
        check("WxPusher：多个 SPT 时不再带 spt 字段", "spt" not in calls[0][1])

        # 4) 上限 10 个
        many = ",".join("SPT_{}".format(i) for i in range(14))
        got = wxpusher.parse_spt({"spt": many})
        check("WxPusher：SPT 最多取 10 个", len(got) == 10, len(got))

        # 5) 全是占位符 → skipped，且一个请求都不发
        calls[:] = []
        res = wxpusher.send({"spt": "SPT_粘这里", "app_token": "AT_填这里",
                             "uids": ["UID_填这里"]}, "正文", "摘要")
        check("WxPusher：占位符算没配，skipped",
              res["skipped"] and not res["ok"], res["detail"])
        check("WxPusher：skipped 时一次请求都不发", len(calls) == 0, len(calls))

        # 5b) 回归：占位符里带空格/括号时，绝不能把碎片（README、1）当令牌发出去
        sample = {"spt": "SPT_扫码拿到的令牌粘这里（见 README 第 1 步）"}
        check("WxPusher：带空格的占位符不会被切碎成假令牌",
              wxpusher.parse_spt(sample) == [], wxpusher.parse_spt(sample))
        calls[:] = []
        res = wxpusher.send(sample, "正文", "摘要")
        check("WxPusher：这种占位符照样 skipped、不发请求",
              res["skipped"] and len(calls) == 0, res["detail"])
        check("WxPusher：非 SPT_ 开头的碎片直接丢掉",
              wxpusher.parse_spt({"spt": "README,SPT_ok,1"}) == ["SPT_ok"],
              wxpusher.parse_spt({"spt": "README,SPT_ok,1"}))

        # 6) 1002 → 熔断
        reply[0] = {"code": 1002, "msg": "appToken错误"}
        res = wxpusher.send({"spt": "SPT_abc"}, "正文", "摘要")
        check("WxPusher：SPT 无效(1002) 触发熔断", res["fatal"] is True, res["detail"])

        # 6b) 实测：SPT 写错时官方返回 1001（msg 带「检查你的SPT」）
        #     —— 必须熔断，而且不能白白多打一次降级请求
        calls[:] = []
        reply[0] = {"code": 1001,
                    "msg": "用户不存在，请检查你的SPT，你可以访问下面的链接查看使用说明"}
        res = wxpusher.send({"spt": "SPT_invalid"}, "正文", "摘要")
        check("WxPusher：SPT 不存在的 1001 也熔断", res["fatal"] is True, res["detail"])
        check("WxPusher：令牌错时不浪费一次降级重试", len(calls) == 1, len(calls))

        # 7) 1001 → 自动降级纯文本重发一次
        calls[:] = []
        seq = [{"code": 1001, "msg": "参数错误"}, {"code": 1000, "msg": "处理成功"}]

        def fake_seq(url, payload, timeout=15, retries=1):
            calls.append((url, payload))
            return 200, json.dumps(seq[len(calls) - 1], ensure_ascii=False)

        wxpusher.http_post_json = fake_seq
        res = wxpusher.send({"spt": "SPT_abc"}, "**正文**", "摘要")
        check("WxPusher：1001 时降级纯文本重发一次", len(calls) == 2, len(calls))
        check("WxPusher：降级后用 contentType=1", calls[-1][1].get("contentType") == 1)
        check("WxPusher：降级成功仍算 ok", res["ok"] is True, res["detail"])

        # 8) 标准推送路径不能被改坏
        calls[:] = []
        wxpusher.http_post_json = fake_post
        reply[0] = {"code": 1000, "msg": "处理成功"}
        res = wxpusher.send({"app_token": "AT_xyz", "uids": ["UID_1"]}, "正文", "摘要")
        check("WxPusher：没配 spt 时仍走标准推送",
              calls[0][0] == wxpusher.STANDARD_API, calls[0][0])
        check("WxPusher：标准推送带 appToken / uids",
              calls[0][1].get("appToken") == "AT_xyz"
              and calls[0][1].get("uids") == ["UID_1"])
        check("WxPusher：标准推送 1000 也算 ok", res["ok"] is True, res["detail"])
    finally:
        wxpusher.http_post_json = real_post


def main():
    tests = (test_school_list, test_jwc_list, test_chem_list, test_xsc_list,
             test_phy_list, test_date_trap, test_nav_filter, test_table_layout,
             test_detail_time, test_store, test_message, test_digest,
             test_quiet_hours,
             test_truncate, test_sources_config, test_unit_name,
             test_wxpusher_channel)
    for test in tests:
        try:
            test()
        except Exception as exc:                     # 单个用例炸掉不影响其它
            check("{} 抛异常".format(test.__name__), False, repr(exc))
    total, passed = len(RESULTS), sum(1 for ok in RESULTS if ok)
    print("\n{} / {} 通过".format(passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

