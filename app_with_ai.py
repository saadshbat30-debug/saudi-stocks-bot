from flask import Flask, jsonify
import requests
from datetime import datetime
from ai_predictor import StockPredictor

app = Flask(__name__)

BOT_TOKEN = "8493081055:AAG5aH7h6kbBjBEXEC_4-dHhqNRINh7iw0U"
CHAT_ID = "8476329457"

# تهيئة منظم الذكاء الاصطناعي
predictor = StockPredictor()

def send_alert(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message}
    try:
        response = requests.post(url, json=data)
        return response.status_code == 200
    except:
        return False

@app.route("/")
def home():
    return """
    <html>
        <body style="text-align: center; padding: 20px; font-family: Arial;">
            <h1>🤖 نظام الأسهم السعودية بالذكاء الاصطناعي</h1>
            <p>✅ النظام يعمل على السحابة!</p>
            <p><a href="/predict">📈 عرض تنبؤات الذكاء الاصطناعي</a></p>
            <p><a href="/test">🧪 اختبار البوت</a></p>
            <p>⏰ """ + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + """</p>
        </body>
    </html>
    """

@app.route("/predict")
def predict_stocks():
    """عرض تنبؤات الذكاء الاصطناعي"""
    sample_price = 150  # سعر افتراضي للعرض
    prediction = predictor.predict_next_day(sample_price)
    
    # إرسال التنبؤ للبوت
    alert_msg = f"🤖 تنبؤ الذكاء الاصطناعي:\n"
    alert_msg += f"📊 السعر المتوقع: {prediction['predicted_price']} ريال\n"
    alert_msg += f"📈 الاتجاه: {prediction['trend']}\n"
    alert_msg += f"🎯 الثقة: {prediction['confidence']}%"
    
    send_alert(alert_msg)
    
    return f"""
    <html>
        <body style="text-align: center; padding: 20px;">
            <h2>🤖 تنبؤات الذكاء الاصطناعي</h2>
            <p>📊 السعر الحالي: {sample_price} ريال</p>
            <p>🎯 السعر المتوقع: {prediction['predicted_price']} ريال</p>
            <p>📈 الاتجاه: {prediction['trend']}</p>
            <p>💪 الثقة: {prediction['confidence']}%</p>
            <p><i>تم إرسال التنبؤ إلى البوت</i></p>
            <a href="/">← العودة</a>
        </body>
    </html>
    """

@app.route("/test")
def test_bot():
    send_alert("🧪 اختبار من النظام الذكي - يعمل بنجاح!")
    return "✅ تم إرسال اختبار إلى البوت"

if __name__ == "__main__":
    print("🚀 بدء النظام بالذكاء الاصطناعي...")
    send_alert("🤖 النظام الذكي يعمل على السحابة!")
    app.run(host="0.0.0.0", port=5000)
git add .
git commit -m "Add AI stock prediction"
git push
# في Termux، أنشئ تطبيقاً خفيفاً جداً
cat > ultra_light.py
from flask import Flask
import requests
import random

app = Flask(__name__)

BOT_TOKEN = "8493081055:AAG5aH7h6kbBjBEXEC_4-dHhqNRINh7iw0U"
CHAT_ID = "8476329457"

@app.route("/")
def home():
    return "🟢 SYSTEM WORKING!"

@app.route("/test")
def test():
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                  json={"chat_id": CHAT_ID, "text": "✅ ULTRA LIGHT TEST"})
    return "TEST SENT!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
echo "Flask==2.3.3
requests==2.31.0" > requirements.txt
echo "Flask==2.3.3
requests==2.31.0" > requirements.txt
cat > app.py << 'EOF'
from flask import Flask
import requests
import random

app = Flask(__name__)

BOT_TOKEN = "8493081055:AAG5aH7h6kbBjBEXEC_4-dHhqNRINh7iw0U"
CHAT_ID = "8476329457"

@app.route("/")
def home():
    return "🟢 SYSTEM WORKING!"

@app.route("/test")
def test():
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                  json={"chat_id": CHAT_ID, "text": "✅ ULTRA LIGHT TEST"})
    return "TEST SENT!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
EOF
echo "Flask==2.3.3
requests==2.31.0" > requirements.txt
git add .
git commit -m "Use ultra light version"
git push
cd trading_bot
git status
git add .
git commit -m "Deploy to render"
git push
pwd
ls -la
cd trading_bot
git add .
git add .
git commit -m "Deploy to render"
git push
