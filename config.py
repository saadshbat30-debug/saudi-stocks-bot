import os

# المنصة تعرض أسهم السوق الأمريكي من ماسح ياهو، في نطاق سعري محدد،
# مرتبة بالأكثر تداولًا اليوم.
MARKETS = {
    "us": {
        "title": "السوق الأمريكي",
        "currency": "$",
        "min_price": float(os.environ.get("MIN_PRICE", "0")),
        "max_price": float(os.environ.get("MAX_PRICE", "60")),
        # بورصات ناسداك ونيويورك وأمكس (بدون OTC)
        "exchanges": ["NMS", "NGM", "NCM", "NYQ", "ASE"],
        "limit": int(os.environ.get("US_LIMIT", "750")),
        # فلاتر السعر في الصفحة: (الاسم، من، إلى)
        "price_bands": [
            ["تحت 1$", 0, 1],
            ["1–5$", 1, 5],
            ["5–20$", 5, 20],
            ["20–60$", 20, 60],
        ],
        "stocks": {},
    },
}

# تيليجرام اختياري: المنصة تعمل بدونه، والتنبيهات تظهر داخل الموقع
# اضبط المتغيرين فقط إذا أردت التنبيهات على البوت أيضًا
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

# نسبة التغير اليومي (%) التي يُرسل عندها تنبيه (الأسهم الصغيرة تتحرك كثيرًا)
ALERT_PCT = float(os.environ.get("ALERT_PCT", "10"))
# التنبيه عندما يكون حجم التداول أضعاف المعتاد
ALERT_VOL_RATIO = float(os.environ.get("ALERT_VOL_RATIO", "3"))
# كل كم ثانية يفحص التنبيهات
ALERT_INTERVAL = int(os.environ.get("ALERT_INTERVAL", "60"))
# مدة تخزين الأسعار مؤقتًا (ثوانٍ) لتجنب الحظر من ياهو
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "20"))
# مدة تخزين التاريخ اليومي المستخدم للمؤشرات
HISTORY_CACHE_SECONDS = int(os.environ.get("HISTORY_CACHE_SECONDS", "600"))


def find_stock(symbol):
    """يرجع (رمز السوق، الاسم) لأي رمز؛ الرموز خارج القوائم تُعرض باسمها."""
    for market_id, market in MARKETS.items():
        if symbol in market["stocks"]:
            return market_id, market["stocks"][symbol]
    return "us", symbol
