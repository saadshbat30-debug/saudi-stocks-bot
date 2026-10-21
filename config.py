import os

# المنصة تعرض كل أسهم السوق الأمريكي من ماسح ياهو بكل الأسعار:
# البورصات المدرجة + سوق OTC (جزء منفصل حتى لا تزاحم أسهمه الصغيرة جدًا الأسهم المدرجة).
MARKETS = {
    "us": {
        "title": "السوق الأمريكي",
        "currency": "$",
        "min_price": float(os.environ.get("MIN_PRICE", "0")),
        # بدون حد أعلى للسعر (اضبط MAX_PRICE لتحديد نطاق)
        "max_price": float(os.environ["MAX_PRICE"]) if os.environ.get("MAX_PRICE") else None,
        # ناسداك، نيويورك، أمكس، آركا، Cboe
        "exchanges": ["NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS"],
        "limit": int(os.environ.get("US_LIMIT", "1000")),
        # سوق OTC (غير المدرجة): الأعلى حجمًا والأكثر ارتفاعًا
        "otc_exchanges": ["PNK", "OQX", "OQB"],
        "otc_limit": int(os.environ.get("OTC_LIMIT", "250")),
        # فلاتر السعر في الصفحة: (الاسم، من، إلى)
        "price_bands": [
            ["تحت 1$", 0, 1],
            ["1–5$", 1, 5],
            ["5–20$", 5, 20],
            ["20–60$", 20, 60],
            ["60–200$", 60, 200],
            ["200$+", 200, 1e12],
        ],
        "stocks": {},
    },
}

# الأسهم قليلة الأسهم الحرة (Low Float): أقل من هذا العدد
LOW_FLOAT = float(os.environ.get("LOW_FLOAT", "20000000"))

# شريط المؤشرات أعلى الصفحة
INDICES = {
    "^DJI": "داو جونز",
    "^IXIC": "ناسداك",
    "^GSPC": "S&P 500",
    "^RUT": "راسل 2000",
}

# تيليجرام اختياري: المنصة تعمل بدونه، والتنبيهات تظهر داخل الموقع
# اضبط المتغيرين فقط إذا أردت التنبيهات على البوت أيضًا
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

# نسبة التغير اليومي (%) التي يُرسل عندها تنبيه (الأسهم الصغيرة تتحرك كثيرًا)
ALERT_PCT = float(os.environ.get("ALERT_PCT", "10"))
# التنبيه عندما يكون حجم التداول أضعاف المعتاد
ALERT_VOL_RATIO = float(os.environ.get("ALERT_VOL_RATIO", "3"))
# التنبيه عندما يتحرك السهم هذه النسبة (%) خلال 5 دقائق (زخم)
ALERT_SPEED = float(os.environ.get("ALERT_SPEED", "3"))
# كل كم ثانية يفحص التنبيهات
ALERT_INTERVAL = int(os.environ.get("ALERT_INTERVAL", "60"))
# مدة تخزين الأسعار مؤقتًا (ثوانٍ) لتجنب الحظر من ياهو
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "20"))
# كل كم ثانية تُحدّث قائمة الأسهم من الماسح (الأسعار نفسها تأتي لحظيًا من البث المباشر)
SCREEN_SECONDS = int(os.environ.get("SCREEN_SECONDS", "60"))
# مدة تخزين التاريخ اليومي المستخدم للمؤشرات
HISTORY_CACHE_SECONDS = int(os.environ.get("HISTORY_CACHE_SECONDS", "600"))


def find_stock(symbol):
    """يرجع (رمز السوق، الاسم) لأي رمز؛ الرموز خارج القوائم تُعرض باسمها."""
    for market_id, market in MARKETS.items():
        if symbol in market["stocks"]:
            return market_id, market["stocks"][symbol]
    return "us", symbol
