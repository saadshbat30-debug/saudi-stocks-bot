import threading
import time
from datetime import date

import requests

from config import ALERT_INTERVAL, ALERT_PCT, BOT_TOKEN, CHAT_ID, MARKETS
from market import get_quotes

_sent = set()  # (الرمز، التاريخ، الاتجاه) حتى لا يتكرر التنبيه في نفس اليوم


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
    today = date.today().isoformat()
    for market in MARKETS.values():
        quotes = get_quotes(list(market["stocks"]))
        for symbol, q in quotes.items():
            if abs(q["change_pct"]) < ALERT_PCT:
                continue
            direction = "up" if q["change_pct"] > 0 else "down"
            key = (symbol, today, direction)
            if key in _sent:
                continue
            arrow = "🟢📈" if direction == "up" else "🔴📉"
            text = (
                f"{arrow} {market['stocks'][symbol]} ({symbol})\n"
                f"السعر: {q['price']} {market['currency']}\n"
                f"التغير: {q['change_pct']:+.2f}%"
            )
            if send_telegram(text):
                _sent.add(key)


def _loop():
    while True:
        try:
            check_alerts()
        except Exception as e:
            print(f"⚠️ خطأ في التنبيهات: {e}")
        time.sleep(ALERT_INTERVAL)


def start_alerts():
    if not BOT_TOKEN or not CHAT_ID:
        print("ℹ️ التنبيهات متوقفة: اضبط BOT_TOKEN و CHAT_ID")
        return
    threading.Thread(target=_loop, daemon=True).start()
