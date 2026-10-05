"""Real-time price alerts over the SAHMK WebSocket stream (Pro plan).

Subscribes to every symbol that has an active alert and resubscribes when the
set changes. The polling loop in scheduler.py stays on as a fallback.
"""
import asyncio
import logging
import threading

from sahmk import SahmkError

from . import alerts, market, storage

log = logging.getLogger(__name__)
_started = False
RESYNC_SECONDS = 15


def _alert_symbols():
    return sorted({a["symbol"] for _, a in storage.all_alerts()})[:200]


async def _on_quote(msg):
    data = msg.get("data") or {}
    symbol = msg.get("symbol") or data.get("symbol")
    price = data.get("price")
    if symbol and price is not None:
        alerts.evaluate(symbol, float(price), data.get("change_percent"), data.get("name"))


async def _supervise():
    task, current = None, []
    while True:
        wanted = _alert_symbols()
        if task and task.done():
            exc = task.exception() if not task.cancelled() else None
            if isinstance(exc, SahmkError) and exc.status_code in (4401, 4403, 401, 403):
                log.error("WebSocket stream not permitted (%s); using polling only.", exc)
                return
            if exc:
                log.warning("Stream stopped: %s", exc)
            task = None
        if wanted != current or task is None:
            if task:
                task.cancel()
                task = None
            current = wanted
            if current:
                log.info("Streaming %d symbols for alerts", len(current))
                task = asyncio.create_task(market.client().stream(current, on_quote=_on_quote))
        await asyncio.sleep(RESYNC_SECONDS)


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=lambda: asyncio.run(_supervise()), name="stream", daemon=True).start()
