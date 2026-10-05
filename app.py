import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, render_template, request
import requests

load_dotenv()  # read SAHMK_API_KEY etc. from a local .env file if present

import backtest
import market_data
from strategy import Params

app = Flask(__name__)

# Telegram is optional; the web page works without it.
BOT_TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID") or os.environ.get("OWNER_CHAT_ID", "")

# Aramco, Al Rajhi, SABIC, STC, SNB, ACWA
DEFAULT_SYMBOLS = os.environ.get("SYMBOLS", "2222,1120,2010,7010,1180,2082")
MAX_SYMBOLS = 10  # keep within the free plan's daily request quota

PARAMS = Params(
    periods=int(os.environ.get("BB_PERIODS", 20)),
    deviations=float(os.environ.get("BB_DEVIATIONS", 2.0)),
    ma_type=os.environ.get("BB_MA_TYPE", "wilder"),
    max_band_width_pct=float(os.environ.get("BB_MAX_WIDTH_PCT", 8.0)),
    consolidation_periods=int(os.environ.get("CONSOLIDATION_PERIODS", 1)),
    rsi_period=int(os.environ.get("RSI_PERIOD", 14)),
    rsi_overbought=float(os.environ.get("RSI_OVERBOUGHT", 70)),
    rsi_oversold=float(os.environ.get("RSI_OVERSOLD", 30)),
)

RIYADH = timezone(timedelta(hours=3))
MOODS = {"bullish": "إيجابي", "bearish": "سلبي", "neutral": "محايد"}


def parse_symbols(text):
    symbols = [s.strip().upper().removesuffix(".SR") for s in text.split(",") if s.strip()]
    return list(dict.fromkeys(symbols))[:MAX_SYMBOLS]


def send_telegram(text):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text},
        timeout=10,
    ).raise_for_status()


@app.route("/")
def home():
    symbols = parse_symbols(request.args.get("symbols", DEFAULT_SYMBOLS))
    rows = [market_data.analyze(s, PARAMS) for s in symbols]
    summary, summary_error = market_data.market_summary()
    gainers, _ = market_data.movers("gainers")
    losers, _ = market_data.movers("losers")

    notices = []
    if not os.environ.get("SAHMK_API_KEY"):
        notices.append("أضف مفتاح سهمك في المتغير SAHMK_API_KEY لعرض البيانات.")
    elif summary_error:
        notices.append(f"تعذّر جلب بيانات السوق: {summary_error}")
    if os.environ.get("SAHMK_API_KEY") and any(r["history_error"] for r in rows):
        notices.append(
            "إشارات بولينجر وRSI تحتاج البيانات التاريخية، وهي متاحة من باقة Starter في سهمك. "
            "الأسعار وحركة السوق تعمل على الباقة المجانية."
        )

    return render_template(
        "index.html",
        rows=rows,
        symbols=symbols,
        summary=summary,
        gainers=gainers,
        losers=losers,
        notices=notices,
        params=PARAMS,
        moods=MOODS,
        now=datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M"),
    )


def float_arg(name, default):
    try:
        value = float(request.args.get(name, default))
        return value if value > 0 else default
    except ValueError:
        return default


@app.route("/backtest")
def backtest_page():
    symbols = parse_symbols(request.args.get("symbols", DEFAULT_SYMBOLS))
    rules = backtest.Rules(
        take_profit_pct=float_arg("tp", 6.0),
        stop_loss_pct=float_arg("sl", 3.0),
        max_hold_days=int(float_arg("days", 20)),
    )

    results = []
    for symbol in symbols:
        close, error = market_data.closes(symbol)
        if close is None or len(close) <= PARAMS.periods:
            results.append({"symbol": symbol, "error": error or "لا تتوفر بيانات كافية", "trades": []})
        else:
            results.append({"symbol": symbol, "error": None, **backtest.run(close, PARAMS, rules)})

    all_trades = [t for r in results for t in r["trades"]]
    overall = backtest.summarize(all_trades, []) if all_trades else None

    notices = []
    if not os.environ.get("SAHMK_API_KEY"):
        notices.append("أضف مفتاح سهمك في المتغير SAHMK_API_KEY لعرض البيانات.")

    return render_template(
        "backtest.html", results=results, overall=overall, symbols=symbols, rules=rules, notices=notices
    )


@app.route("/health")
def health():
    return "🟢 SYSTEM WORKING! ✅ - Saudi Stocks Bot"


@app.route("/test")
def test():
    try:
        send_telegram("✅ TEST FROM RENDER")
        return "✅ TEST SENT TO BOT!"
    except Exception:
        return "❌ ERROR SENDING TEST"


@app.route("/scan")
def scan():
    symbols = parse_symbols(DEFAULT_SYMBOLS)
    alerts = []
    for row in (market_data.analyze(s, PARAMS) for s in symbols):
        if row.get("signal_today"):
            _, side, price, r = row["last_signal"]
            label = "🟢 اختراق صاعد" if side == "BUY" else "🔴 كسر هابط"
            alerts.append(f"{label} {row['symbol']}\nالسعر: {price:.2f}\nRSI: {r:.1f}")
    try:
        if alerts:
            send_telegram("📊 إشارات Bollinger + RSI\n\n" + "\n\n".join(alerts))
    except Exception as e:
        return f"❌ TELEGRAM ERROR: {e}", 500
    return f"✅ SCANNED {len(symbols)} SYMBOLS, {len(alerts)} SIGNALS"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    if os.environ.get("OPEN_BROWSER"):
        import threading
        import webbrowser

        threading.Timer(1.5, webbrowser.open, [f"http://localhost:{port}"]).start()
    print(f"Open http://localhost:{port} in your browser")
    app.run(host="127.0.0.1", port=port)
