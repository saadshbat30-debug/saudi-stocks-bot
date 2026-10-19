import os

# قوائم المتابعة: رمز ياهو ← الاسم المعروض
# الأسهم السعودية في ياهو تنتهي بـ .SR
MARKETS = {
    "us": {
        "title": "السوق الأمريكي",
        "currency": "$",
        "stocks": {
            "AAPL": "Apple",
            "MSFT": "Microsoft",
            "NVDA": "NVIDIA",
            "AMZN": "Amazon",
            "GOOGL": "Alphabet",
            "META": "Meta",
            "TSLA": "Tesla",
            "AMD": "AMD",
            "SPY": "S&P 500 ETF",
            "QQQ": "Nasdaq 100 ETF",
        },
    },
    "sa": {
        "title": "السوق السعودي",
        "currency": "ر.س",
        "stocks": {
            "2222.SR": "أرامكو",
            "1120.SR": "الراجحي",
            "2010.SR": "سابك",
            "7010.SR": "STC",
            "1180.SR": "الأهلي",
            "2082.SR": "أكوا باور",
        },
    },
}

# تيليجرام اختياري: المنصة تعمل بدونه، والتنبيهات تظهر داخل الموقع
# اضبط المتغيرين فقط إذا أردت التنبيهات على البوت أيضًا
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

# نسبة التغير اليومي (%) التي يُرسل عندها تنبيه
ALERT_PCT = float(os.environ.get("ALERT_PCT", "3"))
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
    return ("sa" if symbol.endswith(".SR") else "us"), symbol
