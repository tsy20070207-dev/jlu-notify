# -*- coding: utf-8 -*-
"""吉林大学校内通知推送 —— 基础工具：路径 / JSON / 日志 / HTTP。

关键约束：本机（以及很多同学）跑着 Clash / TUN 模式代理，fake-ip 会把
*.jlu.edu.cn 解析到 198.18.x.x，导致 curl / requests 全部连不上校内站点。
所以这里构造的 opener 一律挂空 ProxyHandler（强制直连），不读环境变量里的
HTTP_PROXY / ALL_PROXY，也不需要额外设置 NO_PROXY。
"""

import gzip
import hashlib
import json
import os
import random
import re
import time
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "run.log")

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 所有出站请求都走直连（校内站点必须直连；推送接口直连同样没问题）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class FetchError(Exception):
    """网络层失败（超时 / DNS / 连接被拒 / 5xx 重试后仍失败）。"""


class Response(object):
    """一次 HTTP GET 的结果。4xx 也算正常返回（便于探测栏目页是否存在）。"""

    __slots__ = ("status", "text", "url", "charset")

    def __init__(self, status, text, url, charset="utf-8"):
        self.status = status
        self.text = text
        self.url = url
        self.charset = charset

    @property
    def ok(self):
        try:
            return 200 <= int(self.status) < 300
        except Exception:
            return False

    def __repr__(self):
        return "<Response {} {} chars>".format(self.status, len(self.text))


# ---------------------------------------------------------------- 路径与 JSON

def pj(*parts):
    """拼接项目内绝对路径。"""
    return os.path.join(BASE_DIR, *parts)


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 时间与哈希

def now_ts():
    return int(time.time())


def ts_text(ts=None, fmt="%Y-%m-%d %H:%M:%S"):
    return time.strftime(fmt, time.localtime(ts if ts is not None else time.time()))


def sha1(text):
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 日志

def log(msg, level="INFO"):
    """带时间戳打印，同时追加到 run.log。"""
    line = "[{}] {:<7} {}".format(time.strftime("%H:%M:%S"), level, msg)
    print(line, flush=True)
    append_log(msg)
    return line


def append_log(msg, path=None):
    try:
        with open(path or LOG_FILE, "a", encoding="utf-8") as f:
            f.write("{} {}\n".format(ts_text(), msg))
    except Exception:
        pass


# ---------------------------------------------------------------- HTTP

def _guess_charset(raw, content_type):
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        return m.group(1).lower()
    m = re.search(rb"charset=[\"']?([\w-]+)", raw[:4096], re.I)
    if m:
        return m.group(1).decode("ascii", "ignore").lower()
    return "utf-8"


def _decode(raw, charset):
    if raw[:3] == b"\xef\xbb\xbf":  # 去掉 BOM，否则首行会带上 \ufeff
        raw = raw[3:]
    for enc in (charset, "utf-8", "gb18030"):
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "ignore")


def http_get(url, timeout=15, retries=2, headers=None):
    """GET 一个页面，返回 Response。

    4xx（401/403/404/410）不抛异常而是返回原状态码，方便探测 "栏目页存不存在"；
    超时 / 5xx / DNS 失败会退避重试，最终仍失败抛 FetchError。
    """
    hdrs = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
    }
    hdrs.update(headers or {})
    last = None
    for attempt in range(int(retries) + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with _OPENER.open(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip" or raw[:2] == b"\x1f\x8b":
                    try:
                        raw = gzip.decompress(raw)
                    except Exception:
                        pass
                charset = _guess_charset(raw, resp.headers.get("Content-Type", ""))
                return Response(resp.status, _decode(raw, charset), resp.geturl(), charset)
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:
                pass
            if exc.code in (401, 403, 404, 410):
                return Response(exc.code, _decode(body, "utf-8"), url, "utf-8")
            last = FetchError("HTTP {} <- {}".format(exc.code, url))
        except Exception as exc:
            last = FetchError("{} <- {}".format(exc.__class__.__name__, url))
        if attempt < int(retries):
            time.sleep(1.2 ** attempt + random.random() * 0.3)
    raise last or FetchError("unknown error <- " + url)


def http_post_json(url, payload, timeout=15, retries=1):
    """POST 一个 JSON，返回 (status, text)。推送通道用。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdrs = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": USER_AGENT,
        "Connection": "close",
    }
    last = None
    for attempt in range(int(retries) + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
            with _OPENER.open(req, timeout=timeout) as resp:
                body = resp.read()
                return resp.status, _decode(body, _guess_charset(body, resp.headers.get("Content-Type", "")))
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:
                pass
            return exc.code, _decode(body, "utf-8")
        except Exception as exc:
            last = exc
            if attempt < int(retries):
                time.sleep(1.0 + random.random())
    raise FetchError("POST 失败 {}: {}".format(url, last))


def sleep_jitter(ms_range=(0, 0)):
    """在给定毫秒区间内随机睡一下，避免多个源同时开火。"""
    low, high = (list(ms_range or (0, 0)) + [0, 0])[:2]
    delay = random.uniform(float(low), float(high)) / 1000.0
    if delay > 0:
        time.sleep(delay)


# ---------------------------------------------------------------- 文本截断

def truncate_bytes(text, limit, suffix="\n…（内容过长已截断）"):
    """按 UTF-8 字节数截断（企业微信按字节算 4096），不会切碎中文。"""
    raw = (text or "").encode("utf-8")
    if len(raw) <= int(limit):
        return text or ""
    tail = suffix.encode("utf-8")
    raw = raw[:max(0, int(limit) - len(tail))]
    return raw.decode("utf-8", "ignore") + suffix


def truncate_chars(text, limit, suffix="…"):
    """按字符数截断（微信推送类接口按字符算）。"""
    text = text or ""
    if len(text) <= int(limit):
        return text
    return text[:max(0, int(limit) - len(suffix))] + suffix

