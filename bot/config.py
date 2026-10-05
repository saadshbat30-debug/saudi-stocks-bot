import os
import secrets
from zoneinfo import ZoneInfo

SAHMK_API_KEY = os.environ.get("SAHMK_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID", "").strip()

# Render sets RENDER_EXTERNAL_URL automatically for web services.
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")

# Secret path + header so only Telegram can call the webhook.
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET") or secrets.token_hex(16)

ALERT_INTERVAL_SECONDS = int(os.environ.get("ALERT_INTERVAL_SECONDS", "60"))
EVENTS_INTERVAL_SECONDS = int(os.environ.get("EVENTS_INTERVAL_SECONDS", "300"))
ENABLE_STREAM = os.environ.get("ENABLE_STREAM", "1") != "0"
QUOTE_CACHE_SECONDS = int(os.environ.get("QUOTE_CACHE_SECONDS", "20"))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))

RIYADH = ZoneInfo("Asia/Riyadh")


def missing_settings():
    missing = []
    if not SAHMK_API_KEY:
        missing.append("SAHMK_API_KEY")
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    return missing
