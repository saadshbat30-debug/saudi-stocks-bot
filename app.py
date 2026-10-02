from flask import Flask, request
import requests

from liquidity import check_symbol

app = Flask(__name__)

BOT_TOKEN = "8493081055:AAG5aH7h6kbBjBEXEC_4-dHhqNRINh7iw0U"
CHAT_ID = "8476329457"

@app.route("/")
def home():
    return "🟢 SYSTEM WORKING! ✅ - Saudi Stocks Bot"

@app.route("/test")
def test():
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": "✅ TEST FROM RENDER"}
        )
        return "✅ TEST SENT TO BOT!"
    except:
        return "❌ ERROR SENDING TEST"

@app.route("/value-scan")
def value_scan():
    # VALUE >= 2 * (MONTHLASTAMOUNTSUM / 20), e.g. /value-scan?symbols=1180,2222
    symbols = request.args.get("symbols", "1180").split(",")
    lines = []
    for symbol in symbols:
        symbol = symbol.strip()
        try:
            r = check_symbol(symbol)
        except Exception as e:
            lines.append(f"❌ {symbol}: {e}")
            continue
        if r["passed"]:
            lines.append(
                f"🔥 {symbol}: value {r['value']:,.0f} = {r['ratio']:.2f}x "
                f"avg 20d ({r['avg_20d_value']:,.0f})"
            )
    hits = [line for line in lines if line.startswith("🔥")]
    if hits:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": "\n".join(hits)}
        )
    return "<br>".join(lines) or "No symbols passed the value filter"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
