"""تجميع تدفق السيولة اللحظي من صفقات Alpaca SIP.

التجميع على وقت استلام الرسائل محلياً داخل التطبيق، بدقة حاوية 100ms.
تصنيف NBBO دقيق فقط عندما تكون الصفقة عند/فوق ask أو عند/تحت bid
وكانت اللقطة السابقة للصفقة وحديثة. خلاف ذلك يستخدم tick rule كتقدير،
والصفقات ذات السعر الثابت تصنف مجهولة ولا تورّث اتجاه الصفقة السابقة.
"""
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime

WINDOWS = (1, 5, 10, 60, 300)
MAX_WATCHED = 20
BUCKET_SECONDS = 0.1
QUOTE_MAX_AGE_SECONDS = 1.5

_lock = threading.RLock()
_active = OrderedDict()
_states = {}
_attached_client_id = None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _epoch(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def classify_trade(price, bid=None, ask=None, quote_age=None, previous_price=None):
    """يرجع (side, method): buy/sell/unknown و nbbo/tick/unknown."""
    p = _number(price)
    b, a = _number(bid), _number(ask)
    fresh_quote = (
        p is not None and b is not None and a is not None
        and b > 0 and a > 0 and b <= a
        and quote_age is not None and -0.05 <= quote_age <= QUOTE_MAX_AGE_SECONDS
    )
    if fresh_quote:
        if p >= a:
            return "buy", "nbbo"
        if p <= b:
            return "sell", "nbbo"

    prev = _number(previous_price)
    if p is not None and prev is not None:
        if p > prev:
            return "buy", "tick"
        if p < prev:
            return "sell", "tick"
    return "unknown", "unknown"


def _new_state():
    return {
        "buckets": deque(),
        "quote": None,
        "last_price": None,
        "last_trade": None,
        "updated_at": time.time(),
    }


def watch(symbol):
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        return
    with _lock:
        _active[symbol] = time.time()
        _active.move_to_end(symbol)
        _states.setdefault(symbol, _new_state())
        while len(_active) > MAX_WATCHED:
            old, _ = _active.popitem(last=False)
            _states.pop(old, None)


def attach(client):
    """يربط معالج الرسائل مرة واحدة بعميل Alpaca الحالي."""
    global _attached_client_id
    if client is None:
        return False
    with _lock:
        if _attached_client_id != id(client):
            client.on_data = on_data
            _attached_client_id = id(client)
    return True


def on_data(message):
    if not isinstance(message, dict):
        return
    kind = message.get("T")
    symbol = str(message.get("S") or "").upper()
    if kind not in ("t", "q") or not symbol:
        return
    received = time.time()

    with _lock:
        if symbol not in _active:
            return
        state = _states.setdefault(symbol, _new_state())

        if kind == "q":
            bid, ask = _number(message.get("bp")), _number(message.get("ap"))
            quote_time = _epoch(message.get("t"))
            if bid is not None and ask is not None and quote_time is not None:
                state["quote"] = {"bid": bid, "ask": ask, "event_time": quote_time, "received_at": received}
                state["updated_at"] = received
            return

        price = _number(message.get("p"))
        size = _number(message.get("s"))
        trade_time = _epoch(message.get("t"))
        if price is None or size is None or size <= 0:
            return
        quote = state.get("quote")
        quote_age = None
        if quote and trade_time is not None:
            quote_age = trade_time - quote["event_time"]
        bid = quote.get("bid") if quote else None
        ask = quote.get("ask") if quote else None
        side, method = classify_trade(price, bid, ask, quote_age, state.get("last_price"))

        key = int(received / BUCKET_SECONDS)
        bucket_start = key * BUCKET_SECONDS
        buckets = state["buckets"]
        if not buckets or buckets[-1]["key"] != key:
            buckets.append({
                "key": key, "start": bucket_start,
                "buy_value": 0.0, "sell_value": 0.0, "unknown_value": 0.0,
                "nbbo_value": 0.0, "tick_value": 0.0,
                "buy_count": 0, "sell_count": 0, "unknown_count": 0, "trade_count": 0,
            })
        bucket = buckets[-1]
        value = price * size
        bucket["trade_count"] += 1
        if side == "buy":
            bucket["buy_value"] += value
            bucket["buy_count"] += 1
        elif side == "sell":
            bucket["sell_value"] += value
            bucket["sell_count"] += 1
        else:
            bucket["unknown_value"] += value
            bucket["unknown_count"] += 1
        if method == "nbbo":
            bucket["nbbo_value"] += value
        elif method == "tick":
            bucket["tick_value"] += value

        cutoff = received - 301
        while buckets and buckets[0]["start"] < cutoff:
            buckets.popleft()

        state["last_price"] = price
        state["last_trade"] = {
            "price": price, "size": int(size), "value": value,
            "received_at": received, "event_time": trade_time,
            "side": side, "method": method,
        }
        state["updated_at"] = received


def _sum_window(buckets, cutoff):
    fields = (
        "buy_value", "sell_value", "unknown_value", "nbbo_value", "tick_value",
        "buy_count", "sell_count", "unknown_count", "trade_count",
    )
    total = {field: 0.0 if field.endswith("_value") else 0 for field in fields}
    for bucket in buckets:
        if bucket["start"] < cutoff:
            continue
        for field in fields:
            total[field] += bucket[field]
    buy = total["buy_value"]
    sell = total["sell_value"]
    classified = buy + sell
    gross = classified + total["unknown_value"]
    total.update({
        "net_value": buy - sell,
        "gross_value": gross,
        "classified_value": classified,
        "buy_pct": (buy / classified * 100) if classified else None,
        "nbbo_coverage_pct": (total["nbbo_value"] / gross * 100) if gross else None,
        "estimated_value": total["tick_value"],
    })
    for field in ("buy_value", "sell_value", "unknown_value", "nbbo_value", "tick_value",
                  "net_value", "gross_value", "classified_value", "estimated_value"):
        total[field] = round(total[field], 2)
    if total["buy_pct"] is not None:
        total["buy_pct"] = round(total["buy_pct"], 1)
    if total["nbbo_coverage_pct"] is not None:
        total["nbbo_coverage_pct"] = round(total["nbbo_coverage_pct"], 1)
    return total


def snapshot(symbol, stream_status=None):
    symbol = str(symbol or "").strip().upper()
    now = time.time()
    with _lock:
        state = _states.get(symbol)
        if state is None:
            state = _new_state()
            _states[symbol] = state
        buckets = list(state["buckets"])
        quote = dict(state["quote"]) if state.get("quote") else None
        last_trade = dict(state["last_trade"]) if state.get("last_trade") else None
    windows = {}
    for seconds in WINDOWS:
        windows[str(seconds)] = _sum_window(buckets, now - seconds)
    quote_out = None
    if quote:
        age = max(0.0, now - quote["received_at"])
        quote_out = {
            "bid": quote["bid"], "ask": quote["ask"],
            "spread": round(quote["ask"] - quote["bid"], 4),
            "age_ms": round(age * 1000, 1),
        }
    last_out = None
    if last_trade:
        last_out = {
            **last_trade,
            "age_ms": round(max(0.0, now - last_trade["received_at"]) * 1000, 1),
            "source_age_ms": round((now - last_trade["event_time"]) * 1000, 1)
                if last_trade["event_time"] is not None else None,
        }
    return {
        "symbol": symbol,
        "server_time": now,
        "windows": windows,
        "quote": quote_out,
        "last_trade": last_out,
        "stream": stream_status or {},
        "window_basis": "وقت استلام الرسائل محلياً؛ حاويات تجميع 100ms",
        "classification": "NBBO عند لمس/تجاوز أفضل عرض أو طلب مع Quote سابقة وحديثة؛ وإلا Tick Rule تقديري؛ السعر الثابت غير مصنف",
    }
