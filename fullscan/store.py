"""تخزين الشموع المغلقة للماسح الشامل في SQLite (data/fullscan.sqlite).

المفتاح (الرمز، الفاصل، وقت بداية الشمعة) فلا تتكرر الشمعة؛ إعادة الإرسال بقيم مختلفة (تصحيح من المصدر)
تحل محل القديمة وتُعدّ. يُحتفظ بآخر KEEP شمعة لكل رمز وفاصل.
"""
import os
import sqlite3
import threading

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "fullscan.sqlite")
KEEP = 400


class Store:
    def __init__(self, path=PATH):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("""CREATE TABLE IF NOT EXISTS bars (symbol TEXT, interval TEXT, t INTEGER,
                o REAL, h REAL, l REAL, c REAL, v REAL, PRIMARY KEY (symbol, interval, t))""")
            self.db.commit()
        self.revisions = 0

    def save(self, interval, by_symbol, revisions=0):
        """by_symbol: {الرمز: [الشموع المغلقة الجديدة أو المصححة فقط]}. revisions: عدد التصحيحات للعدّ."""
        rows = [(sym, interval, b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]) for sym, bars in by_symbol.items() for b in bars]
        with self.lock:
            if rows:
                self.db.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)", rows)
                self.db.commit()
            self.saves = getattr(self, "saves", 0) + 1
            if self.saves % 200 == 0:
                self.trim()
        self.revisions += revisions

    def trim(self):
        """يبقي آخر KEEP شمعة لكل رمز وفاصل."""
        self.db.execute("""DELETE FROM bars WHERE rowid IN (SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER
            (PARTITION BY symbol, interval ORDER BY t DESC) AS rn FROM bars) WHERE rn > ?)""", (KEEP,))
        self.db.commit()

    def load(self, interval):
        out = {}
        with self.lock:
            for sym, t, o, h, l, c, v in self.db.execute(
                    "SELECT symbol,t,o,h,l,c,v FROM bars WHERE interval=? ORDER BY symbol, t", (interval,)):
                out.setdefault(sym, []).append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v})
        return out
