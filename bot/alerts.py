"""Shared alert evaluation used by both the WebSocket stream and the polling loop."""
from . import market, storage, telegram


def evaluate(symbol, price, change_percent=None, name=None):
    fired = 0
    for chat_id, a in storage.all_alerts():
        if a["symbol"] != symbol:
            continue
        hit = price >= a["target"] if a["op"] == ">=" else price <= a["target"]
        # remove_alert returns False if another thread already fired it.
        if not hit or not storage.remove_alert(chat_id, a["id"]):
            continue
        sign = "≥" if a["op"] == ">=" else "≤"
        text = (f"🚨 <b>تنبيه سعري</b>\n{market.esc(name or symbol)} ({symbol})\n"
                f"السعر الآن <b>{market.num(price)}</b> {sign} {market.num(a['target'])}")
        if change_percent is not None:
            text += f"\nالتغير اليوم: {market.signed(change_percent, '%')}"
        telegram.send_message(chat_id, text)
        fired += 1
    return fired
