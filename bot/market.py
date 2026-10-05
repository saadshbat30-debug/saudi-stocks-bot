"""Real Saudi market data from the SAHMK API (Tadawul-licensed)."""
import html
import threading
import time
from datetime import datetime

from sahmk import SahmkClient, SahmkError

from . import config

_client = None
_cache = {}
_cache_lock = threading.Lock()


def client():
    global _client
    if _client is None:
        _client = SahmkClient(config.SAHMK_API_KEY, timeout=15, retries=2)
    return _client


def _cached(key, fn):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < config.QUOTE_CACHE_SECONDS:
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def get_quote(identifier):
    return _cached(("quote", identifier), lambda: client().quote(identifier))


def get_prices(symbols):
    """Return {symbol: price}. Uses one batch call (Starter+), falls back to single quotes."""
    symbols = list(dict.fromkeys(symbols))
    prices = {}
    try:
        for i in range(0, len(symbols), 50):
            batch = client().quotes(symbols[i:i + 50])
            for q in batch.quotes:
                if q.symbol and q.price is not None:
                    prices[q.symbol] = q
    except SahmkError as exc:
        if exc.status_code not in (401, 403):
            raise
        # Plan without batch access: one request per symbol.
        for s in symbols:
            prices[s] = get_quote(s)
    return prices


def is_market_open(now=None):
    """Tadawul trades Sunday–Thursday, 10:00–15:00 Riyadh time (+ closing auction to 15:10)."""
    now = now or datetime.now(config.RIYADH)
    if now.weekday() not in (6, 0, 1, 2, 3):  # Sun..Thu
        return False
    minutes = now.hour * 60 + now.minute
    return 10 * 60 <= minutes <= 15 * 60 + 10


# ---------------------------------------------------------------- formatting

def esc(value):
    return html.escape(str(value)) if value is not None else "—"


def num(value, digits=2):
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def big(value):
    if value is None:
        return "—"
    for size, label in ((1e9, "مليار"), (1e6, "مليون"), (1e3, "ألف")):
        if abs(value) >= size:
            return f"{value / size:,.2f} {label}"
    return f"{value:,.0f}"


def arrow(change):
    if change is None:
        return "⚪️"
    return "🟢" if change > 0 else "🔴" if change < 0 else "⚪️"


def signed(value, suffix=""):
    if value is None:
        return "—"
    return f"{value:+,.2f}{suffix}"


def _when(updated_at, delayed):
    parts = []
    if updated_at:
        try:
            ts = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00")).astimezone(config.RIYADH)
            parts.append(ts.strftime("%Y-%m-%d %H:%M"))
        except ValueError:
            parts.append(esc(updated_at))
    if delayed:
        parts.append("(متأخرة)")
    return " ".join(parts)


def format_quote(q):
    name = q.name or q.name_en or q.symbol
    lines = [
        f"{arrow(q.change)} <b>{esc(name)}</b> ({esc(q.symbol)})",
        "",
        f"💰 السعر: <b>{num(q.price)}</b> ريال",
        f"📊 التغير: {signed(q.change)} ({signed(q.change_percent, '%')})",
        f"🔓 الافتتاح: {num(q.open)}   ↩️ الإغلاق السابق: {num(q.previous_close)}",
        f"⬆️ الأعلى: {num(q.high)}   ⬇️ الأدنى: {num(q.low)}",
        f"📦 الكمية: {big(q.volume)}   💵 القيمة: {big(q.value)}",
    ]
    if q.bid is not None or q.ask is not None:
        lines.append(f"🟩 أفضل طلب: {num(q.bid)}   🟥 أفضل عرض: {num(q.ask)}")
    when = _when(q.updated_at, q.is_delayed)
    if when:
        lines += ["", f"🕒 {when}"]
    return "\n".join(lines)


def market_summary_text(index="TASI"):
    m = _cached(("summary", index), lambda: client().market_summary(index=index))
    title = "تاسي (TASI)" if (m.index or index) == "TASI" else "نمو (NOMU)"
    lines = [
        f"{arrow(m.index_change)} <b>مؤشر {title}</b>",
        "",
        f"📈 القيمة: <b>{num(m.index_value)}</b>",
        f"📊 التغير: {signed(m.index_change)} ({signed(m.index_change_percent, '%')})",
    ]
    if m.total_volume is not None:
        lines.append(f"📦 إجمالي الكمية: {big(m.total_volume)}")
    if m.advancing is not None:
        lines.append(f"🟢 مرتفعة: {m.advancing}   🔴 منخفضة: {m.declining}   ⚪️ ثابتة: {m.unchanged}")
    if m.market_mood:
        lines.append(f"🧭 مزاج السوق: {esc(m.market_mood)}")
    lines.append("")
    lines.append("🟢 السوق مفتوح" if is_market_open() else "🌙 السوق مغلق")
    when = _when(m.timestamp, m.is_delayed)
    if when:
        lines.append(f"🕒 {when}")
    return "\n".join(lines)


_MOVERS = {
    "gainers": ("🚀 الأكثر ارتفاعاً", lambda c, n: c.gainers(limit=n)),
    "losers": ("📉 الأكثر انخفاضاً", lambda c, n: c.losers(limit=n)),
    "volume": ("📦 الأكثر نشاطاً بالكمية", lambda c, n: c.volume_leaders(limit=n)),
    "value": ("💵 الأكثر نشاطاً بالقيمة", lambda c, n: c.value_leaders(limit=n)),
}


def movers_text(kind, limit=10):
    title, fetch = _MOVERS[kind]
    res = _cached(("movers", kind, limit), lambda: fetch(client(), limit))
    lines = [f"<b>{title}</b>", ""]
    for i, s in enumerate(res.stocks, 1):
        extra = f" | {big(s.volume)}" if kind == "volume" else f" | {big(s.value)}" if kind == "value" else ""
        lines.append(
            f"{i}. {arrow(s.change_percent)} <b>{esc(s.name or s.name_en)}</b> ({esc(s.symbol)})\n"
            f"     {num(s.price)} ريال  {signed(s.change_percent, '%')}{extra}"
        )
    if not res.stocks:
        lines.append("لا توجد بيانات حالياً.")
    return "\n".join(lines)


def sectors_text():
    res = _cached(("sectors",), lambda: client().sectors())
    rows = sorted(res.sectors, key=lambda s: s.change_percent or 0, reverse=True)
    lines = ["<b>🏭 أداء القطاعات</b>", ""]
    for s in rows:
        lines.append(f"{arrow(s.change_percent)} {esc(s.name or s.name_en)}: {signed(s.change_percent, '%')}")
    return "\n".join(lines)


def search_text(query):
    res = client().companies(search=query, limit=10)
    results = res.get("results", []) if isinstance(res, dict) else []
    if not results:
        return "لم أجد شركة بهذا الاسم."
    lines = [f"<b>🔎 نتائج البحث عن: {esc(query)}</b>", ""]
    for c in results:
        lines.append(f"• <code>{esc(c.get('symbol'))}</code> {esc(c.get('name') or c.get('name_en'))} — {esc(c.get('market'))}")
    lines += ["", "أرسل رمز الشركة لعرض سعرها."]
    return "\n".join(lines)


def history_text(symbol, days=30):
    from datetime import timedelta
    today = datetime.now(config.RIYADH).date()
    res = client().historical(symbol, from_date=str(today - timedelta(days=days)), to_date=str(today))
    bars = getattr(res, "data", None) or []
    if not bars:
        return "لا توجد بيانات تاريخية."
    closes = [b.close for b in bars if b.close is not None]
    first, last = closes[0], closes[-1]
    pct = (last - first) / first * 100 if first else 0
    lines = [
        f"<b>📅 أداء {esc(symbol)} آخر {days} يوم</b>",
        "",
        f"من {num(first)} إلى {num(last)} ({signed(pct, '%')})",
        f"أعلى إغلاق: {num(max(closes))}   أدنى إغلاق: {num(min(closes))}",
        "",
    ]
    for b in bars[-7:]:
        lines.append(f"<code>{esc(str(b.date)[:10])}</code>  {num(b.close)}  ({big(b.volume)})")
    return "\n".join(lines)
