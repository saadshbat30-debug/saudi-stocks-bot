from flask import Flask
import requests

app = Flask(__name__)

BOT_TOKEN = "8493081055:AAG5aH7h6kbBjBEXEC_4-dHhqNRINh7iw0U"
CHAT_ID = "8476329457"

@app.route("/")
def home():
    return "🟢 SYSTEM WORKING! ✅ - V3"

@app.route("/test")
def test():
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": "✅ TEST FROM RENDER - V3"},
            timeout=10
        )
        return f"✅ Status: {response.status_code} - Message Sent! V3"
    except Exception as e:
        return f"❌ ERROR: {str(e)}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
