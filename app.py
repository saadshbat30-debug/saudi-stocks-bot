import os

from flask import Flask
import requests

from strategy import Params, find_signals

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

# Tadawul tickers on Yahoo Finance: Aramco, Al Rajhi, SABIC, STC, SNB, ACWA
DEFAULT_SYMBOLS = "2222.SR,1120.SR,2010.SR,7010.SR,1180.SR,2082.SR"
SYMBOLS = [s.strip() for s in os.environ.get("SYMBOLS", DEFAULT_SYMBOLS).split(",") if s.strip()]

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


def send_telegram(text):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text},
        timeout=10,
    ).raise_for_status()


def scan():
    import yfinance as yf

    alerts = []
    for symbol in SYMBOLS:
        close = yf.Ticker(symbol).history(period="1y", interval="1d")["Close"].dropna()
        signals = find_signals(close, PARAMS)
        # Only alert on a breakout on the latest bar
        if signals and signals[-1][0] == close.index[-1]:
            _, side, price, r = signals[-1]
            label = "🟢 اختراق صاعد" if side == "BUY" else "🔴 كسر هابط"
            alerts.append(f"{label} {symbol}\nالسعر: {price:.2f}\nRSI: {r:.1f}")
    return alerts


@app.route("/")
def home():
    return "🟢 SYSTEM WORKING! ✅ - Saudi Stocks Bot"


@app.route("/test")
def test():
    try:
        send_telegram("✅ TEST FROM RENDER")
        return "✅ TEST SENT TO BOT!"
    except Exception:
        return "❌ ERROR SENDING TEST"


@app.route("/scan")
def scan_route():
    try:
        alerts = scan()
        if alerts:
            send_telegram("📊 إشارات Bollinger + RSI\n\n" + "\n\n".join(alerts))
        return f"✅ SCANNED {len(SYMBOLS)} SYMBOLS, {len(alerts)} SIGNALS"
    except Exception as e:
        return f"❌ SCAN ERROR: {e}", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
