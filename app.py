import logging
import time
from flask import Flask, abort, jsonify, render_template, request

import breakout
import extras
import options
from alerts import send_telegram, start_alerts
from config import SAUDI_URL, ALERT_PCT, ALERT_SPEED, ALERT_VOL_RATIO, INDICES, LOW_FLOAT, MARKETS, find_stock
import stream
from market import STATUS, raw_quotes, screener_age, watch_night, CHART_INTERVALS, get_chart, get_flow, get_market, get_quotes, name_for, raw_quote, valid_symbol

app = Flask(__name__)
# إخفاء سطور الطلبات العادية حتى تظهر رسائل المنصة المهمة فقط
logging.getLogger("werkzeug").setLevel(logging.WARNING)
# رسائل مكتبة ياهو الداخلية ("possibly delisted" و HTTP 401) مزعجة وتُعالج تلقائيًا؛ أخطاء المنصة تظهر بـ ⚠️
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

MAX_SYMBOLS = 40
TV_INDICES = {"^DJI": "DJ:DJI", "^IXIC": "NASDAQ:IXIC", "^GSPC": "SP:SPX", "^RUT": "TVC:RUT"}


@app.route("/")
@app.route("/market/<market_id>")
def home(market_id="us"):
    if market_id not in MARKETS:
        abort(404)
    return render_template(
        "index.html",
        markets=MARKETS,
        market_id=market_id,
        market=MARKETS[market_id],
        alert_pct=ALERT_PCT,
        alert_vol=ALERT_VOL_RATIO,
        alert_speed=ALERT_SPEED,
        low_float=LOW_FLOAT,
    )


@app.route("/stock/<symbol>")
def stock(symbol):
    symbol = symbol.upper()
    if not valid_symbol(symbol):
        abort(404)
    market_id, name = find_stock(symbol)
    watch_night(symbol)  # يُضاف لجلب أسعار التداول الليلي
    name = INDICES.get(symbol) or name_for(symbol) or name
    extras.ensure_info(symbol)  # الفحص الشرعي والأسهم الحرة لهذا السهم فورًا
    return render_template(
        "stock.html",
        markets=MARKETS,
        market_id=market_id,
        market=MARKETS[market_id],
        symbol=symbol,
        name=name,
        tv_symbol=TV_INDICES.get(symbol, symbol),
    )


@app.route("/api/market/<market_id>")
def api_market(market_id):
    if market_id not in MARKETS:
        abort(404)
    return jsonify(get_market(market_id))


@app.route("/api/chart/<symbol>")
def api_chart(symbol):
    symbol = symbol.upper()
    interval = request.args.get("interval", "5m")
    if not valid_symbol(symbol) or interval not in CHART_INTERVALS:
        abort(404)
    # شموع اليوم تشمل ما قبل الافتتاح وما بعد الإغلاق (مثل موموو)، والشموع اليومية للجلسة الرسمية
    return jsonify(get_chart(symbol, interval, prepost=interval in ("1m", "5m", "15m", "1h")))


@app.route("/api/flow/<symbol>")
def api_flow(symbol):
    symbol = symbol.upper()
    if not valid_symbol(symbol):
        abort(404)
    return jsonify(get_flow(symbol))


@app.route("/api/news/<symbol>")
def api_news(symbol):
    symbol = symbol.upper()
    if not valid_symbol(symbol):
        abort(404)
    return jsonify(extras.get_news(symbol))


@app.route("/api/news")
def api_news_multi():
    """?symbols=A,B,C — أخبار عدة أسهم مدمجة (حتى 10)."""
    symbols = [s.strip().upper() for s in request.args.get("symbols", "").split(",") if s.strip()]
    symbols = list(dict.fromkeys(s for s in symbols if valid_symbol(s)))[:10]
    return jsonify(extras.get_news_for(symbols) if symbols else [])


@app.route("/breakout")
def breakout_page():
    get_market("us")  # يشغّل الماسح والبث ومسح قبل الانطلاق إن لم تُفتح الصفحة الرئيسية بعد
    return render_template("breakout.html", markets=MARKETS, market_id=None)


@app.route("/api/breakout")
def api_breakout():
    return jsonify(breakout.snapshot())


@app.route("/saudi")
def saudi_page():
    """منصة أبو طلال للسوق السعودي داخل نفس الصفحة (تعمل كخادم مستقل على SAUDI_URL)."""
    return render_template("saudi.html", markets=MARKETS, market_id=None, saudi_url=SAUDI_URL, hide_position=True)


@app.route("/options")
@app.route("/options/<symbol>")
def options_page(symbol="SPY"):
    symbol = symbol.strip().upper()
    if not valid_symbol(symbol):
        abort(404)
    return render_template("options.html", markets=MARKETS, market_id=None, symbol=symbol)


@app.route("/api/options/<symbol>")
def api_options(symbol):
    symbol = symbol.strip().upper()
    if not valid_symbol(symbol):
        abort(404)
    q = get_quotes([symbol]).get(symbol) or {}
    data = options.chain(symbol, request.args.get("date"), q.get("price"))
    data["quote"] = {k: q.get(k) for k in ("price", "change_pct", "name", "live")}
    data["quote"]["name"] = data["quote"]["name"] or name_for(symbol)
    return jsonify(data)


@app.route("/api/options_scan")
def api_options_scan():
    """?symbols=SPY,QQQ&dte_min=30&dte_max=45"""
    symbols = [s.strip().upper() for s in request.args.get("symbols", "").split(",") if s.strip()]
    symbols = list(dict.fromkeys(s for s in symbols if valid_symbol(s)))[:15]
    dte_min = request.args.get("dte_min", 0, type=float)
    dte_max = min(request.args.get("dte_max", 60, type=float), 400)
    quotes = get_quotes(symbols) if symbols else {}
    results, errors = options.scan(symbols, dte_min, dte_max, {s: q.get("price") for s, q in quotes.items()})
    info = {}
    for s in symbols:
        q = quotes.get(s) or {}
        p, s20, s50 = q.get("price"), q.get("sma20"), q.get("sma50")
        # الاتجاه: صاعد إذا السعر > متوسط 20 > متوسط 50، وهابط بالعكس
        trend = "up" if p and s20 and s50 and p > s20 > s50 else "down" if p and s20 and s50 and p < s20 < s50 else "side"
        info[s] = {"price": p, "change_pct": q.get("change_pct"), "sma20": s20, "sma50": s50, "rsi": q.get("rsi"), "trend": trend}
    return jsonify({"results": results, "symbols": info, "errors": errors})


@app.route("/api/sharia_list", methods=["GET", "POST", "DELETE"])
def api_sharia_list():
    """قائمة شرعية يستوردها المستخدم بلصق نص الصفحة (مثل قائمة يقين للأسهم الحلال)."""
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        halal, haram, purify = extras.parse_sharia_text(body.get("text", ""))
        if not halal and not haram:
            return jsonify({"error": "لم أجد رموز أسهم في النص — انسخ الصفحة كاملة (Ctrl+A ثم Ctrl+C)"}), 400
        extras.set_sharia_list(halal, haram, (body.get("source") or "قائمة يقين").strip()[:40], purify)
    elif request.method == "DELETE":
        extras.clear_sharia_list()
    return jsonify(extras.sharia_list_info())


@app.route("/api/halts")
def api_halts():
    h = extras.get_halts()
    return jsonify({"items": h["items"], "error": h["error"]})


@app.route("/api/trades/<symbol>")
def api_trades(symbol):
    """آخر الصفقات من البث المباشر (الوقت، السعر، الكمية)."""
    symbol = symbol.upper()
    if not valid_symbol(symbol):
        abort(404)
    stream.watch([symbol])
    return jsonify(stream.tape(symbol))


@app.route("/api/status")
def api_status():
    age = screener_age()
    night_age = time.time() - STATUS["night_at"] if STATUS["night_at"] else None
    return jsonify({
        "screener_age": round(age) if age is not None else None, "screener_error": STATUS["screener_error"],
        "stream": stream.status(),
        "night": {"count": STATUS["night_count"], "age": round(night_age) if night_age is not None else None, "error": STATUS["night_error"]},
        "company_info": extras.info_progress(),
    })


@app.route("/api/indices")
def api_indices():
    quotes = get_quotes(list(INDICES))
    return jsonify([{"symbol": s, "name": n, **quotes[s]} for s, n in INDICES.items() if s in quotes])


@app.route("/api/quotes")
def api_quotes():
    """?symbols=AAPL,MSFT,2222.SR"""
    symbols = [s.strip().upper() for s in request.args.get("symbols", "").split(",") if s.strip()]
    symbols = list(dict.fromkeys(s for s in symbols if valid_symbol(s)))[:MAX_SYMBOLS]
    quotes = get_quotes(symbols) if symbols else {}
    for s, q in quotes.items():  # الأسهم خارج قائمة السوق لا يمر عليها الفحص الشرعي في الماسح
        if "sharia" not in q:
            sh = extras.sharia_for(s)
            q["sharia"], q["sharia_why"], q["sharia_src"], q["purify"] = sh["status"], sh["why"], sh.get("source"), sh.get("purify")
    return jsonify(quotes)


@app.route("/api/raw/<symbol>")
def api_raw(symbol):
    """البيانات الخام من ماسح ياهو لسهم — لمعرفة أسماء الحقول (مثل حقول التداول الليلي)."""
    symbol = symbol.upper()
    q = raw_quote(symbol)
    if not q:
        get_market("us")
        q = raw_quote(symbol)
    return jsonify(q or {"error": "الرمز غير موجود في قائمة الماسح"})


@app.route("/api/raw_quote/<symbol>")
def api_raw_quote(symbol):
    """رد ياهو الخام من نقطة الأسعار — لفحص حقول التداول الليلي (overnight)."""
    symbol = symbol.upper()
    if not valid_symbol(symbol):
        abort(404)
    try:
        q = (raw_quotes([symbol]) or [{}])[0]
    except Exception as e:
        return jsonify({"error": str(e)})
    return jsonify({"overnight_fields": {k: v for k, v in q.items() if "overnight" in k.lower()}, "quote": q})


@app.route("/test")
def test():
    if send_telegram("✅ اختبار من منصة الأسهم"):
        return "✅ تم إرسال رسالة اختبار إلى البوت"
    return "❌ تيليجرام غير مفعّل — المنصة تعمل بدونه، اضبط BOT_TOKEN و CHAT_ID لتفعيله", 500


start_alerts()

if __name__ == "__main__":
    import saudi_runner
    msg = saudi_runner.start()  # منصة أبو طلال للسوق السعودي في نفس النافذة (إن كانت مثبتة)
    print("✅ المنصة تعمل: http://127.0.0.1:5000")
    if msg:
        print(msg)
    app.run(host="0.0.0.0", port=5000)
