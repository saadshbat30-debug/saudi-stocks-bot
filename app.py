"""Web entry point (Render / gunicorn): Telegram webhook + background alerts."""
import logging

from flask import Flask, abort, jsonify, request

from bot import config, handlers, market, scheduler, telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

app = Flask(__name__)


@app.route("/")
def home():
    missing = config.missing_settings()
    if missing:
        return f"⚠️ Saudi Stocks Bot — missing settings: {', '.join(missing)}", 500
    return "🟢 Saudi Stocks Bot is running (SAHMK data)"


@app.route("/health")
def health():
    return jsonify(ok=not config.missing_settings(), market_open=market.is_market_open())


@app.route(f"/webhook/{config.WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != config.WEBHOOK_SECRET:
        abort(403)
    handlers.handle_update(request.get_json(force=True, silent=True) or {})
    return "ok"


def _bootstrap():
    missing = config.missing_settings()
    if missing:
        log.error("Missing environment variables: %s", ", ".join(missing))
        return
    telegram.set_commands(handlers.COMMANDS)
    if config.PUBLIC_URL:
        url = f"{config.PUBLIC_URL}/webhook/{config.WEBHOOK_SECRET}"
        result = telegram.set_webhook(url, config.WEBHOOK_SECRET)
        log.info("Webhook set: %s", result.get("ok"))
    else:
        log.warning("PUBLIC_URL not set; webhook not registered. Use run_polling.py locally.")
    scheduler.start()


_bootstrap()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
