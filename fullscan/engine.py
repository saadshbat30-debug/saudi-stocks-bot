"""محرك الماسح الشامل: يفحص كل الأسهم المؤهلة في قاعدة الرموز على الفاصل المختار.

- السعر منفصل عن المؤشرات: السعر من بيانات المنصة الحالية (يتحدث عند وصول صفقات)، والمؤشرات على الشموع المغلقة.
- «مطابقة حالية» فقط إذا كانت آخر شمعة مغلقة للسهم هي أحدث شمعة مغلقة في السوق (المرجع = أحدث شمعة مغلقة
  وصلت من المصدر لأي سهم). غير ذلك: «لا صفقات في آخر شمعة» أو «لم تُحدَّث بياناته بعد» — ولا يبقى في المطابقات.
- الدورة: بعد إغلاق كل شمعة يُعاد جلب كل الأسهم المؤهلة؛ تكتمل الدورة حين يُجلب كل سهم بعد إغلاق تلك الشمعة.
- التغيرات تُرسل للواجهة برقم تسلسلي (since) فلا يُرسل الجدول كله في كل مرة.
"""
import threading
import time
from array import array

from fullscan import bars as B
from fullscan import indicators as I
from fullscan import universe as U

MEM_KEEP = 300  # شموع مغلقة في الذاكرة لكل رمز (تكفي MACD وRSI ومتوسط الحجم وتسخين المتوسطات)
NAN = float("nan")


def _pack(bars):
    """الشموع في الذاكرة مصفوفات أرقام (الوقت، الإغلاق، الحجم) — ما تحتاجه المؤشرات فقط، لتوفير الذاكرة."""
    bars = bars[-MEM_KEEP:]
    return (array("q", [int(b["t"]) for b in bars]), array("d", [b["c"] for b in bars]),
            array("d", [NAN if b["v"] is None else b["v"] for b in bars]))


def _same(a, b):
    return a == b or (a != a and b != b)


STATUS = {
    "ok": "محدثة", "insufficient": "البيانات غير كافية", "no_trade": "لا صفقات في آخر شمعة",
    "stale": "لم تُحدَّث بعد هذه الشمعة", "error": "تعذر الجلب", "pending": "لم يُفحص بعد",
}


class Engine:
    def __init__(self, provider, store=None, universe_fn=U.load, price_fn=None, clock=time.time, sleep=time.sleep):
        self.provider, self.store, self.universe_fn, self.price_fn = provider, store, universe_fn, price_fn
        self.clock, self.sleep = clock, sleep
        self.lock = threading.RLock()
        self.cfg = {**I.DEFAULTS, "interval": "5m", "mode": "closed"}
        self.running = False
        self.thread = None
        self.symbols, self.meta, self.universe_info = [], {}, {}
        self.universe_at = 0
        self._reset_interval()
        self.provider_state = {"rate_limited_until": 0, "last_error": None, "requests": 0, "symbols_fetched": 0,
                               "started": None, "batches_ms": []}

    # ===== الإعداد =====
    def _reset_interval(self):
        self.closed = {}      # الرمز ← الشموع المغلقة (الوقت، الإغلاق، الحجم) مصفوفات
        self.forming = {}     # الرمز ← الشمعة الجارية (آخر جلب)
        self.fetched = {}     # الرمز ← لحظة آخر جلب (asof)
        self.ind = {}         # الرمز ← قيم المؤشرات على آخر شمعة مغلقة
        self.prov_ind = {}    # الرمز ← قيم المؤشرات إن أُغلقت الشمعة الجارية الآن (وضع مؤقت)
        self.errors = {}      # الرمز ← سبب التعذر
        self.rows = {}        # الرمز ← صف النتيجة الحالي
        self.seq = 0
        self.changed = {}     # الرمز ← آخر تسلسل تغير فيه صفه
        self.reference = None  # أحدث بداية شمعة مغلقة وصلت من المصدر
        self.cycle = {"target": None, "started": None, "completed": None, "duration": None, "last_completed": None}

    def configure(self, raw):
        with self.lock:
            iv = raw.get("interval", self.cfg["interval"])
            if iv not in B.INTERVALS:
                raise ValueError("فاصل غير مدعوم")
            mode = raw.get("mode", self.cfg["mode"])
            new = {**I.clean_cfg({**self.cfg, **raw}), "interval": iv, "mode": "forming" if mode == "forming" else "closed"}
            iv_changed = iv != self.cfg["interval"]
            self.cfg = new
            if iv_changed:
                self._reset_interval()
                if self.store:
                    for s, bars in self.store.load(iv).items():
                        if s in self.meta:
                            self.closed[s] = _pack(bars)
            # الشروط تغيرت: إعادة التقييم من الشموع المحفوظة دون جلب جديد
            for s in list(self.closed):
                self._evaluate(s)
            return dict(self.cfg)

    def load_universe(self, force=False):
        syms, info = self.universe_fn(force) if force else self.universe_fn()
        with self.lock:
            seen, out = set(), []
            for x in syms:  # منع تكرار الرمز
                if x["symbol"] not in seen:
                    seen.add(x["symbol"])
                    out.append(x)
            self.symbols = [x["symbol"] for x in out]
            self.meta = {x["symbol"]: x for x in out}
            self.universe_info, self.universe_at = info, self.clock()

    # ===== التشغيل =====
    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            self.provider_state["started"] = self.clock()
            self.thread = threading.Thread(target=self._loop, name="fullscan", daemon=True)
            self.thread.start()

    def stop(self):
        with self.lock:
            self.running = False

    def _loop(self):
        while self.running:
            try:
                self.step()
            except Exception as e:  # المحرك لا يوقف الخادم
                self.provider_state["last_error"] = f"{type(e).__name__}: {str(e)[:160]}"
                self.sleep(5)

    def target_time(self, now=None):
        """لحظة لازم أن يُجلب السهم بعدها حتى تكون بياناته شاملة لآخر شمعة كان يجب أن تُغلق."""
        now = now or self.clock()
        start = B.latest_slot_start(self.cfg["interval"], now)
        return B.slot_end(start, self.cfg["interval"]) + self._grace() if start is not None else None

    def _grace(self):
        g = getattr(self.provider, "grace_for", None)
        return g(self.cfg["interval"]) if g else self.provider.grace

    def due(self, now=None):
        """الرموز التي تحتاج جلبًا: لم تُجلب، أو جُلبت قبل إغلاق آخر شمعة. الأقدم أولًا."""
        now = now or self.clock()
        target = self.target_time(now)
        with self.lock:
            if target and target <= now and self.cycle["target"] != target:
                self.cycle.update(target=target, started=now, completed=None, duration=None)
            need = [s for s in self.symbols if self.fetched.get(s, 0) < (target or 0) or s not in self.fetched]
        need.sort(key=lambda s: self.fetched.get(s, 0))
        return need, target

    def step(self):
        """دفعة واحدة: يجلب أقدم الرموز المستحقة ويقيّمها. يرجع عدد الرموز المجلوبة."""
        now = self.clock()
        if not self.symbols or now - self.universe_at > U.REFRESH_SECONDS:
            self.load_universe()
        ok, why = self.provider.supported(self.cfg["interval"])
        if not ok:
            self.provider_state["last_error"] = why
            self.sleep(10)
            return 0
        if now < self.provider_state["rate_limited_until"]:
            self.sleep(min(5, self.provider_state["rate_limited_until"] - now))
            return 0
        need, target = self.due(now)
        if not need:
            self._check_cycle(now)
            self.sleep(1)
            return 0
        batch = need[:self.provider.batch_size]
        if not self.provider_state["started"]:
            self.provider_state["started"] = self.clock()
        t0 = time.time()
        try:
            bars, errors, asof = self.provider.history(batch, self.cfg["interval"])
        except Exception as e:
            from fullscan.providers import RateLimited
            if isinstance(e, RateLimited):
                self.provider_state["rate_limited_until"] = self.clock() + e.retry_after
            self.provider_state["last_error"] = f"{type(e).__name__}: {str(e)[:160]}"
            self.sleep(5)
            return 0
        ms = round((time.time() - t0) * 1000)
        with self.lock:
            st = self.provider_state
            st["requests"] += 1
            st["symbols_fetched"] += len(batch)
            st["batches_ms"] = (st["batches_ms"] + [ms])[-50:]
            st["last_error"] = None
        self.ingest(bars, errors, asof, batch)
        self._check_cycle(self.clock())
        self.sleep(self.provider.min_gap)
        return len(batch)

    def ingest(self, bars, errors, asof, batch=None):
        iv = self.cfg["interval"]
        save, revisions = {}, 0
        with self.lock:
            for s in batch or list(bars) + list(errors):
                if s not in self.meta:
                    continue
                self.fetched[s] = asof
                if s in bars:
                    closed, forming = B.split(bars[s], iv, asof, self._grace())
                    # يُحفظ الجديد والمصحح فقط: ما بعد آخر شمعة معروفة، أو شمعة معروفة تغيرت قيمها (الإغلاق أو الحجم)
                    old = self.closed.get(s)
                    last_t = old[0][-1] if old and len(old[0]) else None
                    known = {t: i for i, t in enumerate(old[0])} if old else {}
                    fresh = []
                    for b in closed:
                        if last_t is None or b["t"] > last_t:
                            fresh.append(b)
                        elif b["t"] in known:
                            i = known[b["t"]]
                            if not (_same(old[1][i], b["c"]) and _same(old[2][i], NAN if b["v"] is None else b["v"])):
                                fresh.append(b)
                                revisions += 1
                    self.closed[s], self.forming[s] = _pack(closed), forming
                    self.errors.pop(s, None)
                    save[s] = fresh
                else:
                    self.errors[s] = errors.get(s, "لا بيانات")
            old_ref = self.reference
            for s in save:
                if self.closed.get(s) and len(self.closed[s][0]):
                    t = self.closed[s][0][-1]
                    if self.reference is None or t > self.reference:
                        self.reference = t
            # المرجع تقدّم: كل الأسهم التي لم تُجلب بعد إغلاق الشمعة الجديدة تخرج من المطابقات
            targets = list(self.meta) if self.reference != old_ref else list(batch or [])
            for s in targets:
                self._evaluate(s)
        if self.store and save:
            self.store.save(iv, save, revisions)

    def _evaluate(self, s):
        """يحدّث صف السهم: الحالة، القيم، والمطابقة الحالية والمؤقتة."""
        cfg, closed = self.cfg, self.closed.get(s)
        row = {"symbol": s, "status": "pending", "why": None, "matched": False, "provisional": False}
        if s in self.errors and not (closed and len(closed[0])):
            row.update(status="error", why=self.errors[s])
        elif closed is not None:
            ind, why = I.compute_series(*closed, cfg)
            if ind is None:
                row.update(status="insufficient", why=why)
            else:
                row.update({k: ind[k] for k in ("macd", "signal", "rsi", "vol", "vol_avg", "vol_x", "close", "bar_t", "bars")})
                last_t, ref = ind["bar_t"], self.reference
                if s not in self.fetched:  # شموع محفوظة من تشغيل سابق: لا تُعد حالية قبل جلبها من جديد
                    row["status"] = "stale"
                elif ref is not None and last_t < ref:
                    end = B.slot_end(ref, cfg["interval"]) + self._grace()
                    row["status"] = "no_trade" if self.fetched.get(s, 0) >= end else "stale"
                else:
                    row["status"] = "ok"
                m = I.evaluate(ind, cfg)
                if m is None:
                    row.update(status="insufficient" if row["status"] == "ok" else row["status"],
                               why="قيمة مؤشر مطلوبة غير متاحة (حجم ناقص أو متوسط صفر)")
                row["matched"] = row["status"] == "ok" and m is True
            fm = self.forming.get(s)
            if cfg["mode"] == "forming" and fm is not None and closed and len(closed[0]):
                pind, _ = I.compute_series(list(closed[0]) + [fm["t"]], list(closed[1]) + [fm["c"]],
                                           list(closed[2]) + [NAN if fm["v"] is None else fm["v"]], cfg)
                if pind is not None and I.evaluate(pind, cfg) is True:
                    row["provisional"] = True
                    row["p"] = {k: pind[k] for k in ("macd", "signal", "rsi", "vol_x", "close", "bar_t")}
        prev = self.rows.get(s)
        if prev != row:
            self.seq += 1
            self.changed[s] = self.seq
            self.rows[s] = row

    def _check_cycle(self, now):
        target = self.cycle["target"]
        if not target or self.cycle["completed"]:
            return
        with self.lock:
            if all(self.fetched.get(s, 0) >= target for s in self.symbols):
                self.cycle["completed"] = now
                self.cycle["duration"] = round(now - target, 1)
                self.cycle["last_completed"] = {"target": target, "completed": now, "duration": self.cycle["duration"]}

    # ===== القراءة =====
    def counts(self):
        with self.lock:
            by = {}
            for r in self.rows.values():
                by[r["status"]] = by.get(r["status"], 0) + 1
            target = self.cycle["target"]
            done = sum(1 for s in self.symbols if target and self.fetched.get(s, 0) >= target)
            return {
                "universe": self.universe_info.get("eligible", len(self.symbols)), "eligible": len(self.symbols),
                "fetched_this_cycle": done, "evaluated_current": by.get("ok", 0),
                "matched": sum(1 for r in self.rows.values() if r["matched"]),
                "provisional": sum(1 for r in self.rows.values() if r["provisional"]),
                "failed": by.get("error", 0) + by.get("insufficient", 0), "by_status": by,
                "pending": len(self.symbols) - sum(1 for s in self.symbols if s in self.rows),
                "full_cycle_complete": bool(self.cycle["completed"]),
            }

    def status(self):
        st = self.provider_state
        now = self.clock()
        thr = None
        if st["started"] and st["symbols_fetched"]:
            thr = round(st["symbols_fetched"] / max(1, (now - st["started"])) * 60)
        with self.lock:
            return {
                "running": self.running, "cfg": dict(self.cfg), "counts": self.counts(),
                "provider": {"name": self.provider.name, "label": self.provider.label, "licensed": self.provider.licensed,
                             "live_market_wide": self.provider.live_market_wide, "license_note": self.provider.license_note,
                             "requests": st["requests"], "symbols_fetched": st["symbols_fetched"],
                             "symbols_per_min": thr, "last_error": st["last_error"],
                             "rate_limited_for": max(0, round(st["rate_limited_until"] - now)),
                             "batch_ms_median": sorted(st["batches_ms"])[len(st["batches_ms"]) // 2] if st["batches_ms"] else None,
                             "supported": self.provider.supported(self.cfg["interval"])},
                "universe": {k: self.universe_info.get(k) for k in ("source", "eligible", "nasdaq_rows", "other_rows", "etf", "test",
                                                                    "not_common", "preferred_symbol", "file_created", "at", "error")},
                "reference_bar": self.reference, "cycle": dict(self.cycle), "seq": self.seq,
                "store_revisions": self.store.revisions if self.store else 0,
            }

    def _row_out(self, s):
        r = dict(self.rows[s])
        m = self.meta.get(s, {})
        r["name"], r["exchange"] = m.get("name"), m.get("exchange")
        r["status_label"] = STATUS.get(r["status"], r["status"])
        r["fetched_age"] = round(self.clock() - self.fetched[s]) if s in self.fetched else None
        if self.price_fn:
            p = self.price_fn(s)
            if p:
                r["price"] = p
        return r

    def changes(self, since=0, kind="matched"):
        """التغيرات منذ since: الصفوف المطابقة (أو المؤقتة) الجديدة/المحدثة، والرموز التي خرجت."""
        key = "provisional" if kind == "provisional" else "matched"
        with self.lock:
            upsert, removed = [], []
            for s, q in self.changed.items():
                if q <= since:
                    continue
                if self.rows[s].get(key):
                    upsert.append(self._row_out(s))
                else:
                    removed.append(s)
            return {"seq": self.seq, "upsert": upsert, "removed": removed, "full": since == 0}

    def failures(self, limit=500):
        with self.lock:
            bad = [self._row_out(s) for s, r in self.rows.items() if r["status"] in ("error", "insufficient", "no_trade", "stale")]
        reasons = {}
        for r in bad:
            key = r["status_label"] + (f" — {r['why'].split(':')[0].split('(')[0].strip()}" if r.get("why") else "")
            reasons[key] = reasons.get(key, 0) + 1
        return {"total": len(bad), "reasons": reasons, "rows": bad[:limit]}
