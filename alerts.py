import threading
import time
from datetime import date

import requests

from config import ALERT_INTERVAL, ALERT_PCT, ALERT_SPEED, ALERT_VOL_RATIO, BOT_TOKEN, CHAT_ID, MARKETS
from market import get_market

_sent = set()  # (الرمز، التاريخ، النوع) حتى لا يتكرر التنبيه في نفس اليوم


def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ BOT_TOKEN أو CHAT_ID غير مضبوط")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=10,
        )
        return r.ok
    except requests.RequestException:
        return False


def check_alerts():
    """يجمع التنبيهات الجديدة في رسالة واحدة لكل دورة بدل رسالة لكل سهم."""
    today = date.today().isoformat()
    for market_id, market in MARKETS.items():
        lines, keys = [], []
        for q in get_market(market_id):
            checks = []
            if abs(q["change_pct"]) >= ALERT_PCT:
                checks.append(("move", f"{'🟢' if q['change_pct'] > 0 else '🔴'} {q['change_pct']:+.2f}%"))
            if q.get("speed") is not None and abs(q["speed"]) >= ALERT_SPEED:
                checks.append((f"speed{time.strftime('%H')}", f"🚀 زخم {q['speed']:+.2f}% في 5 دقائق"))
            if q.get("vol_ratio") and q["vol_ratio"] >= ALERT_VOL_RATIO:
                checks.append(("volume", f"💧 سيولة {q['vol_ratio']}×"))
            for kind, text in checks:
                key = (q["symbol"], today, kind)
                if key not in _sent:
                    keys.append(key)
                    lines.append(f"{q['symbol']} · {market['currency']}{q['price']} · {text}")
        if not lines:
            continue
        extra = f"\n… و{len(lines) - 30} تنبيه آخر" if len(lines) > 30 else ""
        if send_telegram(f"🔔 {market['title']}\n" + "\n".join(lines[:30]) + extra):
            _sent.update(keys)


def _loop():
    while True:
        try:
            check_alerts()
        except Exception as e:
            print(f"⚠️ خطأ في التنبيهات: {e}")
        time.sleep(ALERT_INTERVAL)


def start_alerts():
    if not BOT_TOKEN or not CHAT_ID:
        print("ℹ️ تنبيهات تيليجرام متوقفة (اختيارية) — تنبيهات الموقع تعمل")
        return
    threading.Thread(target=_loop, daemon=True).start()
