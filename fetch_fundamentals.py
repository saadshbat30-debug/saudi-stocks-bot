"""Download company fundamentals from SAHMK into fundamentals.zip.

Run on your own machine (uses SAHMK_API_KEY from .env). Saves the raw API
responses for financial statements, ratios and dividends of each symbol so
they can be analysed offline.
"""
import json
import os
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
        data = {"symbol": sym}

        data["financials"], used = first_ok("financials", [
            (h, lambda h=h: to_jsonable(client.financials(sym, history=h, period="annual"))) for h in HISTORIES
        ])
        data["financials_history"] = used

        data["ratios"], used = first_ok("ratios", [
            (h, lambda h=h: client.ratios(sym, history=h, period="annual", metrics="extended")) for h in HISTORIES
        ])
        data["ratios_history"] = used

        data["dividends"], _ = first_ok("dividends", [("all", lambda: to_jsonable(client.dividends(sym)))])

        with open(os.path.join(OUT, f"{sym}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, default=str)

    with zipfile.ZipFile("fundamentals.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(OUT)):
            z.write(os.path.join(OUT, name), name)
    print("\nDone. Send the file fundamentals.zip (in this folder).")


if __name__ == "__main__":
    main()
