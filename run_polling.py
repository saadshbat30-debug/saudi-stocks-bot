"""Run the bot locally with long polling (no public URL needed): python run_polling.py"""
import logging

from bot import config, handlers, scheduler, telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main():
    missing = config.missing_settings()
    if missing:
        raise SystemExit(f"Set these environment variables first: {', '.join(missing)}")
    telegram.delete_webhook()
    telegram.set_commands(handlers.COMMANDS)
    scheduler.start()
    logging.info("Bot is polling. Press Ctrl+C to stop.")
    offset = None
    while True:
        try:
            data = telegram.get_updates(offset)
        except Exception:
            logging.exception("getUpdates failed")
            continue
        for update in data.get("result", []):
            offset = update["update_id"] + 1
            handlers.handle_update(update)


if __name__ == "__main__":
    main()
