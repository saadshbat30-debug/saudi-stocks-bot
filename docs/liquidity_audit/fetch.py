"""يجلب بيانات ياهو الخام ويحفظها CSV كما وصلت (بلا أي تعديل) لإعادة إنتاج الفحص."""
import sys, time, yfinance as yf
SYMS = sys.argv[1:]
for s in SYMS:
    for iv, per in [("1m","7d"),("5m","5d"),("15m","5d"),("30m","5d"),("60m","5d"),("1d","1mo")]:
        for k in range(3):
            try:
                df = yf.Ticker(s).history(period=per, interval=iv, prepost=(iv!="1d"), auto_adjust=False)
                if len(df): break
            except Exception as e: print("ERR", s, iv, e)
            time.sleep(2)
        df[["Open","High","Low","Close","Volume"]].to_csv(f"raw/{s}_{iv}.csv")
        print(s, iv, len(df)); time.sleep(0.4)
