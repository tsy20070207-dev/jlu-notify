# -*- coding: utf-8 -*-
"""SQLite 存储：条目去重 + 每源游标 + 首次基线。

去重策略：
    id = sha1(详情页绝对地址)  —— 用 URL 而不是标题，因为同一条通知会被多个
    站点转载（学校主页 + 学院网站 + 本科生院），URL 相同就只推一次；标题
    相同但 URL 不同的，视为不同文章（学校经常一天发两条同名公示）。

首次运行基线：
    新装/新加源时，把列表页现有的几十条通知全部记成「已推送」，不轰炸手机。
    之后只有真正新出现的条目才会进推送队列。
"""

import os
import sqlite3

from common import now_ts, pj, sha1, ts_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    date        TEXT,
    first_seen  INTEGER NOT NULL,
    pushed      INTEGER NOT NULL DEFAULT 0,
    baseline    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_pending ON items(pushed, first_seen);
CREATE INDEX IF NOT EXISTS idx_items_source  ON items(source, first_seen);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    name          TEXT PRIMARY KEY,
    last_fetch    INTEGER NOT NULL DEFAULT 0,
    last_ok       INTEGER,
    last_error    TEXT,
    baseline_done INTEGER NOT NULL DEFAULT 0,
    last_count    INTEGER NOT NULL DEFAULT 0
);
"""


class Store(object):
    """一个进程一个实例，用完 close()。"""

    def __init__(self, path):
        self.path = path or pj("data", "notify.db")
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # 老库升级：SCHEMA 里的 CREATE TABLE IF NOT EXISTS 不会给已有表补列，
        # 所以这里显式 ALTER 一次（新库会抛 duplicate column，忽略即可）。
        upgraded = False
        try:
            self.conn.execute("ALTER TABLE items ADD COLUMN baseline INTEGER NOT NULL DEFAULT 0")
            upgraded = True
        except sqlite3.OperationalError:
            pass
        if upgraded:
            # 补列后老数据全是 baseline=0 —— 但「每个源的第一批」其实就是当初建基线的那批
            # （同一批共享同一个 first_seen，且都 pushed=1）。不回溯标记的话，升级当天
            # 汇总会把几百条历史通知当成「今天的新通知」。
            self.conn.execute(
                "UPDATE items SET baseline=1 WHERE first_seen = "
                "(SELECT MIN(i2.first_seen) FROM items i2 WHERE i2.source = items.source)")
            self.conn.commit()
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------ 条目

    @staticmethod
    def item_id(item, source=""):
        key = (item.get("url") or "").strip()
        if not key:
            key = "{}|{}".format(source, item.get("title") or "")
        return sha1(key)[:20]

    def add_items(self, source, items, baseline=False):
        """写入条目，返回「本次新出现」的列表（baseline=True 时一律返回空）。"""
        now = now_ts()
        fresh = []
        cur = self.conn.cursor()
        for item in items or []:
            iid = self.item_id(item, source)
            if cur.execute("SELECT 1 FROM items WHERE id=?", (iid,)).fetchone():
                continue
            cur.execute(
                "INSERT INTO items (id, source, title, url, date, first_seen, pushed, baseline) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (iid, source, item.get("title", ""), item.get("url", ""),
                 item.get("date", ""), now, 1 if baseline else 0,
                 1 if baseline else 0),
            )
            if not baseline:
                row = dict(item)
                row.update({"id": iid, "source": source, "first_seen": now})
                fresh.append(row)
        self.conn.commit()
        return fresh

    def pending(self, limit=200):
        rows = self.conn.execute(
            "SELECT * FROM items WHERE pushed=0 ORDER BY first_seen ASC, date DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [dict(r) for r in rows]

    def pending_count(self):
        row = self.conn.execute("SELECT COUNT(*) AS n FROM items WHERE pushed=0").fetchone()
        return int(row["n"]) if row else 0

    def items_between(self, start_ts, end_ts, limit=200):
        """取 [start_ts, end_ts) 之间首次出现的条目（每日汇总用）。

        排除 baseline 条目：新加源会把该源的历史通知以「当天 first_seen」入库，
        不排除的话当天汇总会莫名多出几十条旧通知。
        """
        rows = self.conn.execute(
            "SELECT * FROM items WHERE baseline=0 AND first_seen>=? AND first_seen<? "
            "ORDER BY first_seen ASC, date DESC LIMIT ?",
            (int(start_ts), int(end_ts), int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ 键值状态

    def get_meta(self, key, default=""):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                          (key, str(value)))
        self.conn.commit()

    def mark_pushed(self, ids):
        ids = [i for i in (ids or []) if i]
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        cur = self.conn.execute("UPDATE items SET pushed=1 WHERE id IN ({})".format(marks), ids)
        self.conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------ 源游标

    def source_state(self, name):
        row = self.conn.execute("SELECT * FROM sources WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None

    def touch_source(self, name, ok=True, count=0, error=""):
        """记录一次抓取结果（无论成功失败都要记，否则坏源会被疯狂重试）。"""
        now = now_ts()
        prev = self.source_state(name) or {}
        self.conn.execute("INSERT OR IGNORE INTO sources (name) VALUES (?)", (name,))
        self.conn.execute(
            "UPDATE sources SET last_fetch=?, last_ok=?, last_error=?, last_count=? WHERE name=?",
            (now,
             now if ok else prev.get("last_ok"),
             "" if ok else str(error or "")[:300],
             int(count or 0),
             name),
        )
        self.conn.commit()

    def set_baseline(self, name):
        self.conn.execute("INSERT OR IGNORE INTO sources (name) VALUES (?)", (name,))
        self.conn.execute("UPDATE sources SET baseline_done=1 WHERE name=?", (name,))
        self.conn.commit()

    def baseline_done(self, name):
        row = self.conn.execute(
            "SELECT baseline_done FROM sources WHERE name=?", (name,)).fetchone()
        return bool(row and row["baseline_done"])

    def is_due(self, name, interval_seconds):
        """距上次抓取是否已超过 interval_seconds（新源视为到期）。"""
        row = self.conn.execute("SELECT last_fetch FROM sources WHERE name=?", (name,)).fetchone()
        if not row:
            return True
        return (now_ts() - int(row["last_fetch"] or 0)) >= max(0, int(interval_seconds))

    # ------------------------------------------------------------ 维护

    def prune(self, days=120):
        """删掉已推送且太老的条目，控制数据库体积（默认留 120 天）。"""
        cutoff = now_ts() - int(days) * 86400
        cur = self.conn.execute("DELETE FROM items WHERE pushed=1 AND first_seen < ?", (cutoff,))
        self.conn.commit()
        return cur.rowcount

    def stats(self):
        row = self.conn.execute("SELECT COUNT(*) AS total, SUM(pushed) AS pushed FROM items").fetchone()
        total = int(row["total"] or 0)
        pushed = int(row["pushed"] or 0)
        src = self.conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()
        return {
            "db": self.path,
            "items": total,
            "pushed": pushed,
            "pending": total - pushed,
            "sources": int(src["n"] or 0),
        }

    def source_rows(self):
        out = []
        for r in self.conn.execute("SELECT * FROM sources ORDER BY name").fetchall():
            d = dict(r)
            d["last_fetch_text"] = ts_text(d["last_fetch"]) if d.get("last_fetch") else "从未"
            d["last_ok_text"] = ts_text(d["last_ok"]) if d.get("last_ok") else "-"
            out.append(d)
        return out
