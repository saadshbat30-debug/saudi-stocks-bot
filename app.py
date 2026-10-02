import os

from flask import Flask, jsonify, request
import requests

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def send_message(chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )


def get_price(ticker):
    """Return the latest Tadawul quote for a ticker like "2222" (Aramco).

    Prices come from Yahoo Finance and lag the market by about 15 minutes.
    """
    ticker = ticker.strip().upper()
    symbol = ticker if ticker.endswith(".SR") else f"{ticker}.SR"
    resp = requests.get(
        YAHOO_CHART_URL.format(symbol=symbol),
        params={"range": "1d", "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    results = resp.json().get("chart", {}).get("result") or []
    if not results:
        return None
    meta = results[0]["meta"]
    price = meta.get("regularMarketPrice")
    if price is None:
        return None
    prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
    change_pct = (price - prev_close) / prev_close * 100 if prev_close else None
    return {
        "symbol": symbol,
        "name": meta.get("longName") or meta.get("shortName") or symbol,
        "price": price,
        "currency": meta.get("currency", "SAR"),
        "change_pct": change_pct,
    }


def format_quote(quote):
    text = f"{quote['name']} ({quote['symbol']})\n{quote['price']:.2f} {quote['currency']}"
    if quote["change_pct"] is not None:
        arrow = "🟢" if quote["change_pct"] >= 0 else "🔴"
        text += f"  {arrow} {quote['change_pct']:+.2f}%"
    return text


def handle_command(text):
    parts = text.split()
    if not parts:
        return None
    command = parts[0].split("@")[0].lower()
    if command in ("/start", "/help"):
        return "Send /price followed by a Tadawul ticker, for example: /price 2222"
    if command == "/price":
        if len(parts) < 2:
            return "Usage: /price 2222"
        try:
            quote = get_price(parts[1])
        except requests.RequestException:
            return "❌ Could not reach the price service, try again shortly."
        if quote is None:
            return f"❌ No price found for {parts[1]}"
        return format_quote(quote)
    return None


@app.route("/")
def home():
    return "🟢 SYSTEM WORKING! ✅ - Saudi Stocks Bot"


@app.route("/test")
def test():
    try:
        send_message(CHAT_ID, "✅ TEST FROM RENDER")
        return "✅ TEST SENT TO BOT!"
    except Exception:
        return "❌ ERROR SENDING TEST"


@app.route("/price/<ticker>")
def price(ticker):
    try:
        quote = get_price(ticker)
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 502
    if quote is None:
        return jsonify({"error": f"No price found for {ticker}"}), 404
    return jsonify(quote)


@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}
    message = update.get("message") or {}
    text = message.get("text", "")
    chat_id = message.get("chat", {}).get("id")
    if chat_id and text:
        reply = handle_command(text)
        if reply:
            send_message(chat_id, reply)
    return "ok"


@app.route("/set_webhook")
def set_webhook():
    url = request.host_url.replace("http://", "https://") + "telegram"
    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
        json={"url": url},
        timeout=10,
    )
    return jsonify(resp.json())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
