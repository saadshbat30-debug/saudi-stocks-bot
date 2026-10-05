"""Liquidity lab: records the market, logs pre-registered signals, measures them.

Runs as a background thread while the bot is open on a trading day:
  - every 5 minutes 10:05-15:00 Riyadh: snapshot of every main-market stock
  - 14:30: evaluate the frozen filters (v1) and append signals (never edited)
  - 15:20: record the day's closing prices
Results compare each signal with the equal-weight average of all recorded
main-market stocks over the same window, after 0.31% round-trip commission.
"""
import csv
import math
import os
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone

from sahmk import SahmkError

import market_data

RIYADH = timezone(timedelta(hours=3))
DATA_DIR = os.environ.get("LAB_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "lab"))
VERSION = "v1"
COST = 0.0031  # buy + sell commission
SIGNAL_TIME = (14, 30)
CLOSE_TIME = (15, 20)
SESSION = ((10, 5), (15, 0))
MIN_TRADED_VALUE = 5_000_000
MAX_DATA_AGE = 300  # seconds
MIN_SIGNALS_FOR_VERDICT = 100
HORIZONS = (1, 5)  # sessions after the signal

FILTERS = {
    "F1": {
        "name": "تجميع هادئ",
        "rule": "نسبة الداخل ≥ 65% و صافي السيولة ≥ 1,000,000 و تغير الجلسة بين −1% و +2%",
        "expect": "up",
        "test": lambda s: s["inflow_ratio"] >= 65 and s["net"] >= 1_000_000 and -1 <= s["change"] <= 2,
    },
    "F2": {
        "name": "سيولة مطاردة",
        "rule": "نسبة الداخل ≥ 65% و تغير الجلسة ≥ +5%",
        "expect": "up",
        "test": lambda s: s["inflow_ratio"] >= 65 and s["change"] >= 5,
    },
    "F3": {
        "name": "تصريف مخفي",
        "rule": "نسبة الداخل ≤ 35% و صافي السيولة ≤ −1,000,000 و تغير الجلسة ≥ 0%",
        "expect": "down",
        "test": lambda s: s["inflow_ratio"] <= 35 and s["net"] <= -1_000_000 and s["change"] >= 0,
    },
}

SNAPSHOT_FIELDS = ["time", "symbol", "name", "price", "change", "volume", "net", "updated_at"]
SIGNAL_FIELDS = ["date", "time", "filter", "version", "symbol", "name", "price", "change",
                 "inflow_ratio", "net", "inflow", "outflow", "buy_trades", "sell_trades", "data_age"]
REJECT_FIELDS = ["date", "time", "symbol", "reason"]

status = {"running": False, "last_snapshot": None, "last_error": None, "universe": 0}
_lock = threading.Lock()


def path(*parts):
    return os.path.join(DATA_DIR, *parts)


def now_riyadh():
    return datetime.now(RIYADH)


def is_trading_day(dt):
    return dt.weekday() in (6, 0, 1, 2, 3)  # Sunday..Thursday


def _append(file, fields, rows):
    os.makedirs(os.path.dirname(file), exist_ok=True)
    new = not os.path.exists(file)
    with open(file, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def _read(file):
    if not os.path.exists(file):
        return []
    with open(file, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# --- universe -----------------------------------------------------------------

def is_excluded(symbol, name):
    """Main market common stocks only: drop REITs, funds and anything outside 1xxx-8xxx."""
    if not symbol.isdigit() or len(symbol) != 4 or symbol[0] == "9":
        return True
    if 4330 <= int(symbol) <= 4399:  # REIT range
        return True
    return any(w in (name or "") for w in ("ريت", "REIT", "صندوق"))


def universe(client, today):
    cache = path(f"universe-{today}.txt")
    if os.path.exists(cache):
        with open(cache) as f:
            return [s for s in f.read().split() if s]
    symbols, offset = [], 0
    while True:
        page = client.companies(market="TASI", limit=100, offset=offset)
        items = page.get("results") or page.get("companies") or []
        for c in items:
            sym = str(c.get("symbol", ""))
            if sym and not is_excluded(sym, c.get("name") or c.get("name_en")):
                symbols.append(sym)
        offset += len(items)
        if not items or offset >= (page.get("total") or page.get("count") or 0):
            break
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(cache, "w") as f:
        f.write("\n".join(symbols))
    return symbols


# --- recording ----------------------------------------------------------------

def snapshot(client, symbols, now):
    rows = []
    for i in range(0, len(symbols), 50):
        for q in client.quotes(symbols[i:i + 50]).quotes:
            rows.append({
                "time": now.strftime("%H:%M"), "symbol": q.symbol, "name": q.name, "price": q.price,
                "change": q.change_percent, "volume": q.volume, "net": q.net_liquidity, "updated_at": q.updated_at,
            })
    stamped = [r for r in rows if r["updated_at"]]
    live = [r for r in stamped if str(r["updated_at"])[:10] == now.date().isoformat()]
    if stamped and not live:
        return []  # market holiday: nothing traded today
    _append(path("snapshots", f"{now.date()}.csv"), SNAPSHOT_FIELDS, rows)
    return rows


def _age_seconds(updated_at, now):
    try:
        t = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=RIYADH)
        return (now - t).total_seconds()
    except ValueError:
        return None


def evaluate_signals(client, rows, now):
    """Apply the frozen filters to the 14:30 snapshot and append signals."""
    today = now.date().isoformat()
    shortlist = []
    for r in rows:
        price, change, net, vol = _num(r["price"]), _num(r["change"]), _num(r["net"]), _num(r["volume"])
        if price is None or change is None or vol is None or price * vol < MIN_TRADED_VALUE:
            continue
        if abs(net or 0) >= 1_000_000 or change >= 5:
            shortlist.append(r["symbol"])

    signals, rejects = [], []
    for sym in shortlist:
        try:
            q = client.quote(sym)
        except SahmkError as e:
            rejects.append({"date": today, "time": now.strftime("%H:%M"), "symbol": sym, "reason": str(e)})
            continue
        liq = q.liquidity
        age = _age_seconds(q.updated_at, now)
        if liq is None or not (liq.inflow_value or liq.outflow_value):
            reason = "لا توجد بيانات سيولة"
        elif age is not None and age > MAX_DATA_AGE:
            reason = f"بيانات قديمة ({int(age)} ث)"
        else:
            reason = None
        if reason:
            rejects.append({"date": today, "time": now.strftime("%H:%M"), "symbol": sym, "reason": reason})
            continue
        inflow, outflow = liq.inflow_value or 0, liq.outflow_value or 0
        s = {
            "date": today, "time": now.strftime("%H:%M"), "version": VERSION, "symbol": sym, "name": q.name,
            "price": q.price, "change": q.change_percent or 0, "inflow": inflow, "outflow": outflow,
            "net": inflow - outflow, "inflow_ratio": round(inflow / (inflow + outflow) * 100, 2),
            "buy_trades": liq.inflow_trades, "sell_trades": liq.outflow_trades,
            "data_age": None if age is None else int(age),
        }
        for key, f in FILTERS.items():
            if f["test"](s):
                signals.append({**s, "filter": key})
    _append(path("signals.csv"), SIGNAL_FIELDS, signals)
    _append(path("rejected.csv"), REJECT_FIELDS, rejects)
    return signals


def record_closes(client, symbols, now):
    rows = snapshot(client, symbols, now)
    closes = [{"date": now.date().isoformat(), "symbol": r["symbol"], "close": r["price"]} for r in rows if r["price"]]
    _append(path("closes.csv"), ["date", "symbol", "close"], closes)
    return closes


def _done(marker):
    return os.path.exists(path("done", marker))


def _mark(marker):
    os.makedirs(path("done"), exist_ok=True)
    open(path("done", marker), "w").close()


def tick(client, now):
    """One scheduler step; safe to call every minute."""
    if not is_trading_day(now):
        return
    hm = (now.hour, now.minute)
    day = now.date().isoformat()
    in_session = SESSION[0] <= hm <= SESSION[1]
    slot = f"{day}-{now.hour:02d}{now.minute // 5 * 5:02d}"
    need_signal = hm >= SIGNAL_TIME and not _done(f"{day}-signals") and hm <= SESSION[1]
    need_close = hm >= CLOSE_TIME and not _done(f"{day}-close")
    if not (in_session and not _done(slot)) and not need_signal and not need_close:
        return
    symbols = universe(client, day)
    status["universe"] = len(symbols)
    if need_close:
        record_closes(client, symbols, now)
        _mark(f"{day}-close")
        return
    rows = snapshot(client, symbols, now)
    _mark(slot)
    status["last_snapshot"] = now.strftime("%Y-%m-%d %H:%M")
    if need_signal and rows:
        evaluate_signals(client, rows, now)
        _mark(f"{day}-signals")


def run_forever(interval=60):
    status["running"] = True
    while True:
        try:
            tick(market_data.client(), now_riyadh())
            status["last_error"] = None
        except Exception as e:  # keep recording on the next minute
            status["last_error"] = f"{now_riyadh():%H:%M} {e}"
        time.sleep(interval)


def start_background():
    with _lock:
        if status["running"]:
            return
        threading.Thread(target=run_forever, daemon=True).start()
        status["running"] = True


# --- measurement --------------------------------------------------------------

def _closes_by_date():
    out = {}
    for r in _read(path("closes.csv")):
        c = _num(r["close"])
        if c:
            out.setdefault(r["date"], {})[r["symbol"]] = c
    return out


def _prices_at(day, hhmm):
    """Prices of every stock in the snapshot taken at hhmm (fallback: latest before it)."""
    rows = _read(path("snapshots", f"{day}.csv"))
    best = {}
    for r in rows:
        if r["time"] <= hhmm:
            p = _num(r["price"])
            if p:
                best[r["symbol"]] = p
    return best


def outcomes():
    closes = _closes_by_date()
    days = sorted(closes)
    results = []
    for s in _read(path("signals.csv")):
        later = [d for d in days if d > s["date"]]
        entry = _num(s["price"])
        start_prices = _prices_at(s["date"], s["time"])
        row = dict(s)
        for h in HORIZONS:
            row[f"ret{h}"] = row[f"bench{h}"] = row[f"net{h}"] = None
            if len(later) < h or not entry:
                continue
            end = closes[later[h - 1]]
            if s["symbol"] not in end:
                continue
            stock = end[s["symbol"]] / entry - 1
            bench = [end[k] / v - 1 for k, v in start_prices.items() if k in end]
            if not bench:
                continue
            b = sum(bench) / len(bench)
            excess = stock - b
            if FILTERS.get(s["filter"], {}).get("expect") == "down":
                excess = -excess
            row[f"ret{h}"], row[f"bench{h}"], row[f"net{h}"] = stock, b, excess - COST
        results.append(row)
    return results


def _stats(values):
    n = len(values)
    if not n:
        return {"n": 0}
    mean = sum(values) / n
    sd = statistics.stdev(values) if n > 1 else 0.0
    return {
        "n": n,
        "hit": sum(v > 0 for v in values) / n * 100,
        "mean": mean * 100,
        "median": statistics.median(values) * 100,
        "t": mean / (sd / math.sqrt(n)) if sd else None,
        "best": max(values) * 100,
        "worst": min(values) * 100,
    }


def summary(results=None):
    results = outcomes() if results is None else results
    out = {}
    for key, f in FILTERS.items():
        rows = [r for r in results if r["filter"] == key]
        per = {}
        for h in HORIZONS:
            done = [r[f"net{h}"] for r in rows if r[f"net{h}"] is not None]
            half = len(done) // 2
            st = _stats(done)
            st["first_half"], st["second_half"] = _stats(done[:half]), _stats(done[half:])
            st["pending"] = len(rows) - len(done)
            per[h] = st
        five = per[5]
        if five["n"] < MIN_SIGNALS_FOR_VERDICT:
            verdict = f"قيد القياس ({five['n']} من {MIN_SIGNALS_FOR_VERDICT} إشارة مكتملة)"
        elif (five["mean"] > 0 and (five["t"] or 0) > 2
              and five["first_half"].get("mean", 0) > 0 and five["second_half"].get("mean", 0) > 0):
            verdict = "نجح المعيار المعلن مسبقاً"
        else:
            verdict = "لم ينجح"
        out[key] = {**f, "key": key, "signals": len(rows), "h": per, "verdict": verdict}
    return out


def today_overview(now=None):
    now = now or now_riyadh()
    day = now.date().isoformat()
    rows = _read(path("snapshots", f"{day}.csv"))
    if not rows:
        files = sorted(os.listdir(path("snapshots"))) if os.path.isdir(path("snapshots")) else []
        if not files:
            return None
        day = files[-1][:-4]
        rows = _read(path("snapshots", files[-1]))
    last_time = max(r["time"] for r in rows)
    latest = [r for r in rows if r["time"] == last_time and _num(r["net"]) is not None]
    latest.sort(key=lambda r: _num(r["net"]), reverse=True)
    total = sum(_num(r["net"]) for r in latest)
    return {"day": day, "time": last_time, "count": len(latest), "total_net": total,
            "top_in": [r for r in latest if _num(r["net"]) > 0][:10],
            "top_out": [r for r in latest[::-1] if _num(r["net"]) < 0][:10],
            "snapshots": len({r["time"] for r in rows})}
