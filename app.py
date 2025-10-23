from flask import Flask
import requests

app = Flask(__name__)

BOT_TOKEN = "8493081055:AAG5aH7h6kbBjBEXEC_4-dHhqNRINh7iw0U"
CHAT_ID = "8476329457"

@app.route("/")
def home():
    return "🎯 BOT SERVER WORKING!"

@app.route("/test")
def test():
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": "✅ TEST FROM NEW SERVER"})
    return "Message sent to bot!"

if __name__ == "__main__":
    print("Starting server...")
    app.run(host="0.0.0.0", port=5000)
