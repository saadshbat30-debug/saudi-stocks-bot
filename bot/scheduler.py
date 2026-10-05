"""Background loop: price alerts during trading hours + daily open/close summaries."""
import logging
import threading
import time
from datetime import datetime

from . import config, market, storage, telegram

log = logging.getLogger(__name__)
_started = False


def check_alerts():
    alerts = storage.all_alerts()
    if not alerts:
        return 0
    prices = market.get_prices([a["symbol"] for _, a in alerts])
    fired = 0
    for chat_id, a in alerts:
        q = prices.get(a["symbol"])
        if not q or q.price is None:
            continue
        hit = q.price >= a["target"] if a["op"] == ">=" else q.price <= a["target"]
        if not hit:
            continue
        sign = "≥" if a["op"] == ">=" else "≤"
        telegram.send_message(
            chat_id,
            f"🚨 <b>تنبيه سعري</b>\n{market.esc(q.name or q.symbol)} ({q.symbol})\n"
            f"السعر الآن <b>{market.num(q.price)}</b> {sign} {market.num(a['target'])}\n"
            f"التغير اليوم: {market.signed(q.change_percent, '%')}",
        )
        storage.remove_alert(chat_id, a["id"])
        fired += 1
    return fired


def _daily_report(title):
    text = f"<b>{title}</b>\n\n{market.market_summary_text('TASI')}\n\n{market.movers_text('gainers', 5)}\n\n{market.movers_text('losers', 5)}"
    telegram.send_message(config.OWNER_CHAT_ID, text)


def _loop():
    sent = set()
    while True:
        try:
            now = datetime.now(config.RIYADH)
            if market.is_market_open(now):
                check_alerts()
            if config.OWNER_CHAT_ID and now.weekday() in (6, 0, 1, 2, 3):
                day = now.date().isoformat()
                hm = now.hour * 60 + now.minute
                if 10 * 60 + 5 <= hm < 10 * 60 + 30 and ("open", day) not in sent:
                    _daily_report("🔔 افتتاح السوق")
                    sent.add(("open", day))
                if 15 * 60 + 20 <= hm < 16 * 60 and ("close", day) not in sent:
                    _daily_report("🏁 إغلاق السوق")
                    sent.add(("close", day))
        except Exception:
            log.exception("Scheduler iteration failed")
        time.sleep(config.ALERT_INTERVAL_SECONDS)


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="alerts", daemon=True).start()
    log.info("Alert scheduler started (every %ss)", config.ALERT_INTERVAL_SECONDS)
