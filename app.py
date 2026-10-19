from flask import Flask, abort, jsonify, render_template, request

from alerts import send_telegram, start_alerts
from config import ALERT_PCT, ALERT_SPEED, ALERT_VOL_RATIO, INDICES, MARKETS, find_stock
from market import CHART_INTERVALS, get_chart, get_flow, get_market, get_quotes, name_for, valid_symbol

app = Flask(__name__)

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
    )


@app.route("/stock/<symbol>")
def stock(symbol):
    symbol = symbol.upper()
    if not valid_symbol(symbol):
        abort(404)
    market_id, name = find_stock(symbol)
    name = INDICES.get(symbol) or name_for(symbol) or name
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
    return jsonify(get_chart(symbol, interval, prepost=interval != "1d"))


@app.route("/api/flow/<symbol>")
def api_flow(symbol):
    symbol = symbol.upper()
    if not valid_symbol(symbol):
        abort(404)
    return jsonify(get_flow(symbol))


@app.route("/api/indices")
def api_indices():
    quotes = get_quotes(list(INDICES))
    return jsonify([{"symbol": s, "name": n, **quotes[s]} for s, n in INDICES.items() if s in quotes])


@app.route("/api/quotes")
def api_quotes():
    """?symbols=AAPL,MSFT,2222.SR"""
    symbols = [s.strip().upper() for s in request.args.get("symbols", "").split(",") if s.strip()]
    symbols = list(dict.fromkeys(s for s in symbols if valid_symbol(s)))[:MAX_SYMBOLS]
    return jsonify(get_quotes(symbols) if symbols else {})


@app.route("/test")
def test():
    if send_telegram("✅ اختبار من منصة الأسهم"):
        return "✅ تم إرسال رسالة اختبار إلى البوت"
    return "❌ تيليجرام غير مفعّل — المنصة تعمل بدونه، اضبط BOT_TOKEN و CHAT_ID لتفعيله", 500


start_alerts()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
