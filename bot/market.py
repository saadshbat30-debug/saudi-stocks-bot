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
    for size, label in ((1e12, "تريليون"), (1e9, "مليار"), (1e6, "مليون"), (1e3, "ألف")):
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


# ------------------------------------------------------------ Pro features

def _dt(value):
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(config.RIYADH).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return esc(str(value)[:16])


def company_text(identifier):
    c = client().company(get_quote(identifier).symbol)
    lines = [f"<b>🏢 {esc(c.name or c.name_en)}</b> ({esc(c.symbol)})", ""]
    if c.sector or c.industry:
        lines.append(f"القطاع: {esc(c.sector)} — {esc(c.industry)}")
    if c.current_price is not None:
        lines.append(f"السعر الحالي: <b>{num(c.current_price)}</b> ريال")
    f = c.fundamentals
    if f:
        lines += [
            "",
            "<b>📊 الأساسيات</b>",
            f"القيمة السوقية: {big(f.market_cap)}",
            f"مكرر الربحية: {num(f.pe_ratio)}   ربحية السهم: {num(f.eps)}",
            f"القيمة الدفترية: {num(f.book_value)}   السعر/الدفترية: {num(f.price_to_book)}",
            f"بيتا: {num(f.beta)}",
            f"أعلى/أدنى 52 أسبوع: {num(f.fifty_two_week_high)} / {num(f.fifty_two_week_low)}",
        ]
    t = c.technicals
    if t:
        lines += [
            "",
            "<b>📈 المؤشرات الفنية</b>",
            f"RSI(14): {num(t.rsi_14)}   MACD: {num(t.macd_line)} / {num(t.macd_signal)}",
            f"متوسط 50 يوم: {num(t.fifty_day_average)}",
        ]
        if t.price_direction:
            lines.append(f"الاتجاه: {esc(t.price_direction)}")
    v = c.valuation
    if v and v.fair_price is not None:
        conf = f" (ثقة {num(v.fair_price_confidence, 0)}%)" if v.fair_price_confidence is not None else ""
        lines += ["", f"⚖️ القيمة العادلة: <b>{num(v.fair_price)}</b>{conf}"]
    a = c.analysts
    if a and (a.target_mean is not None or a.consensus):
        lines += [
            "",
            "<b>🎯 المحللون</b>",
            f"السعر المستهدف: {num(a.target_mean)} (من {num(a.target_low)} إلى {num(a.target_high)})",
            f"التوصية: {esc(a.consensus)}   عدد المحللين: {esc(a.num_analysts)}",
        ]
    return "\n".join(lines)


def financials_text(identifier):
    symbol = get_quote(identifier).symbol
    res = client().financials(symbol)
    rows = res.income_statements[:4]
    if not rows:
        return "لا توجد قوائم مالية متاحة."
    lines = [f"<b>🧾 قائمة الدخل — {esc(symbol)}</b>", ""]
    for r in rows:
        margin = f" (هامش {r.net_income / r.total_revenue * 100:.1f}%)" if r.net_income is not None and r.total_revenue else ""
        lines += [
            f"<b>{esc(str(r.report_date)[:10])}</b>",
            f"  الإيرادات: {big(r.total_revenue)}",
            f"  إجمالي الربح: {big(r.gross_profit)}",
            f"  الربح التشغيلي: {big(r.operating_income)}",
            f"  صافي الربح: {big(r.net_income)}{margin}",
        ]
    return "\n".join(lines)


def dividends_text(identifier):
    symbol = get_quote(identifier).symbol
    d = client().dividends(symbol)
    lines = [f"<b>💸 التوزيعات — {esc(symbol)}</b>", ""]
    if d.trailing_12m_yield is not None:
        lines.append(f"عائد آخر 12 شهر: <b>{num(d.trailing_12m_yield)}%</b> ({num(d.trailing_12m_dividends)} ريال)")
    if d.upcoming:
        lines += ["", "<b>القادمة</b>"]
        for p in d.upcoming:
            lines.append(f"• {num(p.value)} ريال — الأحقية {esc(p.eligibility_date)} — التوزيع {esc(p.distribution_date)}")
    if d.history:
        lines += ["", "<b>السابقة</b>"]
        for p in d.history[:6]:
            lines.append(f"• {esc(p.distribution_date or p.announcement_date)}: {num(p.value)} ريال ({esc(p.period)})")
    if len(lines) == 2:
        lines.append("لا توجد توزيعات مسجلة.")
    return "\n".join(lines)


_SENTIMENT = {"positive": "🟢", "negative": "🔴", "neutral": "⚪️"}


def format_event(e):
    icon = _SENTIMENT.get(str(e.sentiment or "").lower(), "📰")
    return (f"{icon} <b>{esc(e.stock_name or e.symbol)}</b> ({esc(e.symbol)}) — {esc(e.event_type)}\n"
            f"{esc(e.description)}\n🕒 {_dt(e.article_date or e.created_at)}")


def events_text(identifier=None, limit=5):
    symbol = get_quote(identifier).symbol if identifier else None
    res = client().events(symbol=symbol, limit=limit)
    title = f"📰 آخر أخبار وإعلانات {esc(symbol)}" if symbol else "📰 آخر أخبار وإعلانات السوق"
    if not res.events:
        return f"<b>{title}</b>\n\nلا توجد أحداث حالياً."
    return f"<b>{title}</b>\n\n" + "\n\n".join(format_event(e) for e in res.events)


def depth_text(identifier):
    symbol = get_quote(identifier).symbol
    d = client().depth(symbol, levels=5)
    lines = [
        f"<b>📚 عمق السوق — {esc(symbol)}</b>",
        f"أفضل طلب: {num(d.best_bid)}   أفضل عرض: {num(d.best_ask)}   الفارق: {num(d.spread, 3)}",
        "",
        "<code>   الطلبات (شراء)  |  العروض (بيع)</code>",
    ]
    for i in range(max(len(d.bids), len(d.asks))):
        b = d.bids[i] if i < len(d.bids) else None
        a = d.asks[i] if i < len(d.asks) else None
        left = f"{big(b.quantity):>9} @ {num(b.price):>7}" if b else " " * 19
        right = f"{num(a.price):>7} @ {big(a.quantity)}" if a else ""
        lines.append(f"<code>{left} | {right}</code>")
    if d.level_imbalance is not None:
        side = "🟢 ضغط شراء" if d.level_imbalance > 0 else "🔴 ضغط بيع" if d.level_imbalance < 0 else "⚪️ متوازن"
        lines += ["", f"{side} (توازن {num(d.level_imbalance)})"]
    lines.append(f"🕒 {_dt(d.updated_at)}")
    return "\n".join(lines)


def trades_text(identifier, limit=15):
    symbol = get_quote(identifier).symbol
    t = client().trades(symbol, limit=limit)
    lines = [f"<b>⚡️ آخر الصفقات — {esc(symbol)}</b>", ""]
    for e in t.events:
        side = "🟢" if e.side == "buy" else "🔴" if e.side == "sell" else "⚪️"
        when = _dt(e.event_time or e.timestamp)[-5:]
        lines.append(f"<code>{when}</code> {side} {num(e.price)} × {big(e.quantity)}")
    if not t.events:
        lines.append("لا توجد صفقات حالياً.")
    if t.summary and t.summary.trade_value is not None:
        lines += ["", f"إجمالي القيمة: {big(t.summary.trade_value)}   الكمية: {big(t.summary.trade_quantity)}"]
    return "\n".join(lines)
