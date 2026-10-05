"""Turns Telegram updates into replies."""
import logging
import re

from sahmk import SahmkError, SahmkRateLimitError, SahmkAmbiguousIdentifierError, SahmkUnknownIdentifierError

from . import market, storage, telegram

log = logging.getLogger(__name__)

COMMANDS = [
    ("start", "القائمة الرئيسية"),
    ("market", "ملخص مؤشر تاسي"),
    ("nomu", "ملخص مؤشر نمو"),
    ("gainers", "الأكثر ارتفاعاً"),
    ("losers", "الأكثر انخفاضاً"),
    ("volume", "الأكثر نشاطاً بالكمية"),
    ("value", "الأكثر نشاطاً بالقيمة"),
    ("sectors", "أداء القطاعات"),
    ("quote", "سعر سهم: /quote 2222"),
    ("search", "بحث عن شركة: /search الراجحي"),
    ("history", "أداء آخر 30 يوم: /history 2222"),
    ("watch", "إضافة لقائمة المتابعة: /watch 2222 1120"),
    ("unwatch", "حذف من المتابعة: /unwatch 2222"),
    ("watchlist", "أسعار قائمة المتابعة"),
    ("alert", "تنبيه سعري: /alert 2222 > 30"),
    ("alerts", "عرض التنبيهات"),
    ("delalert", "حذف تنبيه: /delalert رقم"),
    ("help", "المساعدة"),
]

HELP = """<b>🇸🇦 بوت السوق السعودي — بيانات حقيقية من سهمك</b>

أرسل <b>رمز السهم</b> أو <b>اسم الشركة</b> مباشرة (مثل <code>2222</code> أو <code>الراجحي</code>) لعرض السعر اللحظي.

<b>السوق</b>
/market — مؤشر تاسي
/nomu — مؤشر نمو
/gainers — الأكثر ارتفاعاً
/losers — الأكثر انخفاضاً
/volume — الأكثر نشاطاً بالكمية
/value — الأكثر نشاطاً بالقيمة
/sectors — أداء القطاعات

<b>الأسهم</b>
/quote 2222 — سعر سهم
/search أرامكو — بحث عن رمز شركة
/history 2222 — أداء آخر 30 يوم

<b>المتابعة والتنبيهات</b>
/watch 2222 1120 — إضافة أسهم للمتابعة
/unwatch 2222 — حذف سهم
/watchlist — أسعار قائمتك
/alert 2222 &gt; 30 — نبهني إذا تجاوز 30
/alert 2222 &lt; 25 — نبهني إذا نزل تحت 25
/alerts — تنبيهاتك
/delalert abc123 — حذف تنبيه

⏰ التنبيهات تُفحص تلقائياً أثناء التداول (الأحد–الخميس 10:00–15:10)."""

MENU = {
    "inline_keyboard": [
        [{"text": "📈 تاسي", "callback_data": "market"}, {"text": "🌱 نمو", "callback_data": "nomu"}],
        [{"text": "🚀 الأكثر ارتفاعاً", "callback_data": "gainers"}, {"text": "📉 الأكثر انخفاضاً", "callback_data": "losers"}],
        [{"text": "📦 الأكثر كمية", "callback_data": "volume"}, {"text": "💵 الأكثر قيمة", "callback_data": "value"}],
        [{"text": "🏭 القطاعات", "callback_data": "sectors"}, {"text": "⭐️ قائمتي", "callback_data": "watchlist"}],
        [{"text": "🔔 تنبيهاتي", "callback_data": "alerts"}, {"text": "❓ مساعدة", "callback_data": "help"}],
    ]
}

# Arabic keywords that map to commands when typed without a slash.
ARABIC = {
    "السوق": "market", "تاسي": "market", "المؤشر": "market", "نمو": "nomu",
    "الرابحة": "gainers", "الاكثر ارتفاعا": "gainers", "الأكثر ارتفاعاً": "gainers",
    "الخاسرة": "losers", "الاكثر انخفاضا": "losers", "الأكثر انخفاضاً": "losers",
    "القطاعات": "sectors", "قائمتي": "watchlist", "تنبيهاتي": "alerts",
    "مساعدة": "help", "القائمة": "start",
}
ARABIC_PREFIX = {"سعر": "quote", "بحث": "search", "تاريخ": "history", "تابع": "watch", "تنبيه": "alert"}


def handle_update(update):
    try:
        if "callback_query" in update:
            cq = update["callback_query"]
            telegram.answer_callback(cq["id"])
            chat_id = cq["message"]["chat"]["id"]
            reply(chat_id, cq.get("data", ""), "")
        elif "message" in update and "text" in update["message"]:
            msg = update["message"]
            chat_id = msg["chat"]["id"]
            command, args = parse(msg["text"])
            reply(chat_id, command, args)
    except Exception:
        log.exception("Failed handling update")


def parse(text):
    text = text.strip()
    if text.startswith("/"):
        head, _, args = text[1:].partition(" ")
        return head.split("@")[0].lower(), args.strip()
    if text in ARABIC:
        return ARABIC[text], ""
    head, _, args = text.partition(" ")
    if head in ARABIC_PREFIX and args:
        return ARABIC_PREFIX[head], args.strip()
    return "quote", text


def reply(chat_id, command, args):
    storage.register_chat(chat_id)
    try:
        text, markup = dispatch(chat_id, command, args)
    except SahmkRateLimitError:
        text, markup = "⏳ تم تجاوز حد الطلبات في سهمك، حاول بعد قليل.", None
    except SahmkAmbiguousIdentifierError:
        text, markup = "🤔 الاسم يطابق أكثر من شركة، جرّب الرمز أو /search", None
    except SahmkUnknownIdentifierError:
        text, markup = "❌ لم أجد هذا السهم. جرّب /search", None
    except SahmkError as exc:
        log.warning("SAHMK error: %s (%s)", exc, exc.status_code)
        if exc.status_code == 404:
            text = "❌ لم أجد هذا السهم. جرّب /search"
        elif exc.status_code in (401, 403):
            text = "🔒 هذه الميزة غير متاحة في اشتراك سهمك الحالي أو المفتاح غير صحيح."
        else:
            text = "⚠️ تعذّر جلب البيانات من سهمك حالياً، حاول لاحقاً."
        markup = None
    except Exception:
        log.exception("Unexpected error for %s %r", command, args)
        text, markup = "⚠️ حدث خطأ غير متوقع، حاول مرة أخرى.", None
    telegram.send_message(chat_id, text, markup)


def dispatch(chat_id, command, args):
    if command in ("start", "menu"):
        return "👋 أهلاً بك في بوت السوق السعودي\nاختر من القائمة أو أرسل رمز السهم:", MENU
    if command == "help":
        return HELP, None
    if command == "market":
        return market.market_summary_text("TASI"), None
    if command == "nomu":
        return market.market_summary_text("NOMU"), None
    if command in ("gainers", "losers", "volume", "value"):
        return market.movers_text(command), None
    if command == "sectors":
        return market.sectors_text(), None
    if command == "quote":
        if not args:
            return "أرسل الرمز هكذا: /quote 2222", None
        return market.format_quote(market.get_quote(args)), None
    if command == "search":
        if not args:
            return "أرسل اسم الشركة: /search الراجحي", None
        return market.search_text(args), None
    if command == "history":
        if not args:
            return "أرسل الرمز: /history 2222", None
        return market.history_text(market.get_quote(args).symbol), None
    if command == "watch":
        return watch(chat_id, args), None
    if command == "unwatch":
        symbols = args.split()
        if not symbols:
            return "أرسل الرمز: /unwatch 2222", None
        left = storage.remove_from_watchlist(chat_id, symbols)
        return f"🗑 تم الحذف. قائمتك الآن: {', '.join(left) or 'فارغة'}", None
    if command == "watchlist":
        return watchlist_text(chat_id), None
    if command == "alert":
        return alert(chat_id, args), None
    if command == "alerts":
        return alerts_text(chat_id), None
    if command == "delalert":
        if storage.remove_alert(chat_id, args.strip()):
            return "🗑 تم حذف التنبيه.", None
        return "لم أجد تنبيهاً بهذا الرقم. اعرض تنبيهاتك عبر /alerts", None
    return "لم أفهم الأمر. أرسل /help", None


def watch(chat_id, args):
    names = args.split()
    if not names:
        return "أرسل الرموز: /watch 2222 1120"
    symbols = []
    for n in names:
        symbols.append(market.get_quote(n).symbol)
    wl = storage.add_to_watchlist(chat_id, symbols)
    return f"⭐️ تمت الإضافة. قائمتك: {', '.join(wl)}"


def watchlist_text(chat_id):
    wl = storage.get_watchlist(chat_id)
    if not wl:
        return "قائمتك فارغة. أضف أسهماً عبر: /watch 2222 1120"
    quotes = market.get_prices(wl)
    lines = ["<b>⭐️ قائمة المتابعة</b>", ""]
    for s in wl:
        q = quotes.get(s)
        if not q:
            lines.append(f"• {market.esc(s)}: —")
            continue
        lines.append(
            f"{market.arrow(q.change_percent)} <b>{market.esc(q.name or q.name_en)}</b> ({s})\n"
            f"     {market.num(q.price)} ريال  {market.signed(q.change_percent, '%')}"
        )
    return "\n".join(lines)


ALERT_RE = re.compile(r"^(\S+)\s*(>=|<=|>|<)?\s*([\d.]+)$")


def alert(chat_id, args):
    m = ALERT_RE.match(args.strip())
    if not m:
        return "الصيغة: /alert 2222 &gt; 30  أو  /alert 2222 &lt; 25"
    ident, op, target = m.group(1), m.group(2), float(m.group(3))
    q = market.get_quote(ident)
    if not op:
        op = ">=" if q.price is None or target >= q.price else "<="
    op = {">": ">=", "<": "<="}.get(op, op)
    a = storage.add_alert(chat_id, q.symbol, op, target)
    direction = "يصل أو يتجاوز" if op == ">=" else "ينزل إلى أو تحت"
    return (f"🔔 تم إنشاء التنبيه <code>{a['id']}</code>\n"
            f"سأنبهك عندما {direction} سعر {market.esc(q.name or q.symbol)} ({q.symbol}) "
            f"<b>{market.num(target)}</b> ريال.\nالسعر الحالي: {market.num(q.price)}")


def alerts_text(chat_id):
    alerts = storage.get_alerts(chat_id)
    if not alerts:
        return "لا توجد تنبيهات. أنشئ تنبيهاً: /alert 2222 &gt; 30"
    lines = ["<b>🔔 تنبيهاتك</b>", ""]
    for a in alerts:
        sign = "≥" if a["op"] == ">=" else "≤"
        lines.append(f"<code>{a['id']}</code>  {a['symbol']} {sign} {market.num(a['target'])}")
    lines += ["", "للحذف: /delalert رقم_التنبيه"]
    return "\n".join(lines)
