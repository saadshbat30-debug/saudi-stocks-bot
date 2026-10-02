import os

from flask import Flask
import requests
from sahmk import SahmkClient, SahmkError

app = Flask(__name__)

BOT_TOKEN = "8493081055:AAG5aH7h6kbBjBEXEC_4-dHhqNRINh7iw0U"
CHAT_ID = "8476329457"
SAHMK_API_KEY = os.environ.get("SAHMK_API_KEY")


def send_telegram(text):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text},
        timeout=10,
    )


def sahmk_client():
    if not SAHMK_API_KEY:
        raise SahmkError("SAHMK_API_KEY is not set")
    return SahmkClient(SAHMK_API_KEY)

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

@app.route("/price/<symbol>")
def price(symbol):
    try:
        q = sahmk_client().quote(symbol)
        text = f"📈 {q.get('name_en', symbol)} ({symbol}): {q['price']} SAR ({q['change_percent']}%)"
        send_telegram(text)
        return text
    except Exception as e:
        return f"❌ ERROR: {e}", 500

@app.route("/market")
def market():
    try:
        m = sahmk_client().market_summary(index="TASI")
        text = f"🇸🇦 TASI: {m['index_value']} ({m['index_change_percent']}%)"
        send_telegram(text)
        return text
    except Exception as e:
        return f"❌ ERROR: {e}", 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
