from flask import Flask, abort, jsonify, render_template

from alerts import send_telegram, start_alerts
from config import ALERT_PCT, MARKETS, find_stock
from market import get_quotes

app = Flask(__name__)


def tradingview_symbol(symbol):
    if symbol.endswith(".SR"):
        return "TADAWUL:" + symbol[:-3]
    return symbol


@app.route("/")
@app.route("/market/<market_id>")
def home(market_id="us"):
    if market_id not in MARKETS:
        abort(404)
    return render_template(
        "index.html", markets=MARKETS, market_id=market_id, market=MARKETS[market_id], alert_pct=ALERT_PCT
    )


@app.route("/stock/<symbol>")
def stock(symbol):
    found = find_stock(symbol)
    if not found:
        abort(404)
    market_id, name = found
    return render_template(
        "stock.html",
        markets=MARKETS,
        market_id=market_id,
        market=MARKETS[market_id],
        symbol=symbol,
        name=name,
        tv_symbol=tradingview_symbol(symbol),
    )


@app.route("/api/quotes/<market_id>")
def api_quotes(market_id):
    if market_id not in MARKETS:
        abort(404)
    return jsonify(get_quotes(list(MARKETS[market_id]["stocks"])))


@app.route("/test")
def test():
    if send_telegram("✅ اختبار من منصة الأسهم"):
        return "✅ تم إرسال رسالة اختبار إلى البوت"
    return "❌ فشل الإرسال — تأكد من BOT_TOKEN و CHAT_ID", 500


start_alerts()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
