import sys, glob, os
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from datetime import time as T
RAW = os.path.join(HERE, "raw")
if not os.path.isdir(RAW):  # البيانات الخام محفوظة مضغوطة في المستودع (13MB ← ~1MB)
    import tarfile
    with tarfile.open(os.path.join(HERE, "raw_yahoo_2026-11-15.tar.xz")) as t:
        t.extractall(HERE, filter="data")
RES = os.path.join(HERE, "results")
SYMS = sorted({os.path.basename(p).split("_")[0] for p in glob.glob(RAW + "/*_1m.csv")})
def load(sym, iv):
    df = pd.read_csv(f"{RAW}/{sym}_{iv}.csv", index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
    return df.dropna(subset=["Close"])
def session(ts):
    t = ts.time()
    return "pre" if t < T(9,30) else "reg" if t < T(16,0) else "post"
def to_bars(df):  # نفس تحويل market.get_chart
    off = 3*3600
    return [{"time": int(ts.timestamp())+off, "open": float(r.Open), "high": float(r.High), "low": float(r.Low),
             "close": float(r.Close), "volume": int(r.Volume or 0)} for ts, r in df.iterrows()]
