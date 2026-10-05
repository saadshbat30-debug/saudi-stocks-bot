"""Download company fundamentals and daily prices from SAHMK into fundamentals.zip.

Run on your own machine (uses SAHMK_API_KEY from .env). Saves the raw API
responses for financial statements, ratios, dividends and company info, plus
daily price history, so they can be analysed offline. Parts already
downloaded are skipped on later runs.
"""
import csv
import json
import os
from datetime import date, timedelta
import sys
import zipfile

from dotenv import load_dotenv
from sahmk import SahmkClient, SahmkError

SYMBOLS = [
    "1120", "1180", "1010", "1150", "1060",  # banks
    "2222", "2010", "2020", "1211", "2350", "2290", "2310", "3030",  # energy & materials
    "7010", "7020", "7030",  # telecom
    "4190", "4001", "2280", "2050", "4280", "4013",  # consumer & health
    "8010", "2082",  # insurance & utilities
]
HISTORIES = ["max", "10y", "5y", "3y"]  # longest the plan allows wins
PRICE_START = date(2004, 1, 1)
PRICE_CHUNK_DAYS = 600  # the API returns at most ~500 bars per request
OUT = "fundamentals"


def first_ok(label, calls):
    """Try each call in order and return (result, None) for the first that works."""
    errors = []
    for name, call in calls:
        try:
            return call(), name
        except SahmkError as e:
            errors.append(f"{name}: {e}")
    print(f"    {label}: failed ({'; '.join(errors[-2:])})")
    return None, None


def prices_complete(path):
    """True if the file exists and reaches the last two weeks."""
    if not os.path.exists(path):
        return False
    with open(path) as f:
        last = f.read().strip().splitlines()[-1].split(",")[0]
    try:
        return date.fromisoformat(last) >= date.today() - timedelta(days=14)
    except ValueError:
        return False


def fetch_prices(client, sym):
    """Daily bars from PRICE_START to today, fetched in chunks."""
    rows, start, today = {}, PRICE_START, date.today()
    while start <= today:
        end = min(start + timedelta(days=PRICE_CHUNK_DAYS), today)
        try:
            hist = client.historical(sym, from_date=start.isoformat(), to_date=end.isoformat(), interval="1d")
            for p in hist.data:
                rows[p.date] = [p.date, p.open, p.high, p.low, p.close, p.adjusted_close, p.volume]
        except SahmkError as e:
            if getattr(e, "status_code", None) == 403 and not rows:
                start = end + timedelta(days=1)  # range before the plan's limit; move on
                continue
            print(f"    prices {start}..{end}: {e}")
        start = end + timedelta(days=1)
    return [rows[d] for d in sorted(rows)]


def to_jsonable(obj):
    if hasattr(obj, "raw"):
        return obj.raw
    return obj


def main():
    load_dotenv()
    key = os.environ.get("SAHMK_API_KEY")
    if not key:
        sys.exit("SAHMK_API_KEY is missing - put it in the .env file first.")
    client = SahmkClient(key)
    os.makedirs(OUT, exist_ok=True)

    for i, sym in enumerate(SYMBOLS, 1):
        print(f"[{i}/{len(SYMBOLS)}] {sym}")
        path = os.path.join(OUT, f"{sym}.json")
        data = {"symbol": sym}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

        if not data.get("company"):
            data["company"], _ = first_ok("company", [("info", lambda: to_jsonable(client.company(sym)))])

        prices = os.path.join(OUT, f"{sym}_prices.csv")
        if not prices_complete(prices):
            rows = fetch_prices(client, sym)
            if rows:
                with open(prices, "w", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["Date", "Open", "High", "Low", "Close", "AdjClose", "Volume"])
                    w.writerows(rows)
                print(f"    prices: {len(rows)} days from {rows[0][0]}")

        if data.get("financials") and data.get("ratios") and "dividends" in data:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1, default=str)
            continue

        data["financials"], used = first_ok("financials", [
            (h, lambda h=h: to_jsonable(client.financials(sym, history=h, period="annual"))) for h in HISTORIES
        ])
        data["financials_history"] = used

        data["ratios"], used = first_ok("ratios", [
            (h, lambda h=h: client.ratios(sym, history=h, period="annual", metrics="extended")) for h in HISTORIES
        ])
        data["ratios_history"] = used

        data["dividends"], _ = first_ok("dividends", [("all", lambda: to_jsonable(client.dividends(sym)))])

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, default=str)

    with zipfile.ZipFile("fundamentals.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(OUT)):
            z.write(os.path.join(OUT, name), name)
    print("\nDone. Send the file fundamentals.zip (in this folder).")


if __name__ == "__main__":
    main()
