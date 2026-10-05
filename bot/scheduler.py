"""Background loop: polling price alerts, watchlist news, daily open/close summaries."""
import logging
import threading
import time
from datetime import datetime

from . import alerts, config, market, storage, telegram

log = logging.getLogger(__name__)
_started = False


def check_alerts():
    symbols = {a["symbol"] for _, a in storage.all_alerts()}
    if not symbols:
        return 0
    fired = 0
    for symbol, q in market.get_prices(symbols).items():
        if q.price is not None:
            fired += alerts.evaluate(symbol, q.price, q.change_percent, q.name)
    return fired


_seen_events = set()
_events_primed = False


def _event_key(e):
    return (e.symbol, e.created_at or e.article_date, (e.description or "")[:80])


def check_events():
    """Send new company announcements to chats watching that stock."""
    global _events_primed
    watch = storage.all_watchlists()
    if not watch:
        return 0
    res = market.client().events(limit=50)
    new = [e for e in res.events if _event_key(e) not in _seen_events]
    _seen_events.update(_event_key(e) for e in res.events)
    if not _events_primed:  # don't flood old news on startup
        _events_primed = True
        return 0
    sent = 0
    for e in new:
        for chat_id, symbols in watch.items():
            if e.symbol in symbols:
                telegram.send_message(chat_id, "🔔 <b>خبر جديد في قائمتك</b>\n\n" + market.format_event(e))
                sent += 1
    return sent


def _daily_report(title):
    text = f"<b>{title}</b>\n\n{market.market_summary_text('TASI')}\n\n{market.movers_text('gainers', 5)}\n\n{market.movers_text('losers', 5)}"
    telegram.send_message(config.OWNER_CHAT_ID, text)


def _loop():
    sent = set()
    last_events = 0
    while True:
        try:
            now = datetime.now(config.RIYADH)
            if market.is_market_open(now):
                check_alerts()
            if time.time() - last_events >= config.EVENTS_INTERVAL_SECONDS:
                last_events = time.time()
                try:
                    check_events()
                except Exception as exc:
                    log.warning("Events check failed: %s", exc)
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
    if config.ENABLE_STREAM:
        from . import stream
        stream.start()
    log.info("Alert scheduler started (every %ss)", config.ALERT_INTERVAL_SECONDS)
