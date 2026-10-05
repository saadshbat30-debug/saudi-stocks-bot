# -*- coding: utf-8 -*-
"""
sample_data.py — توليد بيانات تجريبية لاختبار البوت بدون تكرتشارت
==================================================================

يُنشئ ملفات CSV الثمانية في مجلد data/ بنفس الأعمدة المتوقعة من تكرتشارت.

    python sample_data.py            # إنشاء البيانات مرة واحدة
    python sample_data.py --loop 30  # تحديث البيانات كل 30 ثانية (لتجربة المراقبة اللحظية)

⚠️ تنبيه: سيستبدل الملفات الموجودة في data/ — لا تشغّله على مجلد بياناتك الحقيقية.
"""

import argparse
import csv
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

# أسهم سعودية للتجربة: (الرمز، الاسم، السعر التقريبي)
STOCKS = [
    ("2222", "أرامكو السعودية", 27.4),
    ("1120", "الراجحي", 93.5),
    ("2010", "سابك", 68.2),
    ("7010", "اس تي سي", 41.8),
    ("1180", "الأهلي", 35.6),
    ("2280", "المراعي", 54.3),
    ("4190", "جرير", 13.9),
    ("1211", "معادن", 48.7),
    ("2082", "أكوا باور", 410.0),
    ("4013", "سليمان الحبيب", 290.0),
    ("1150", "الإنماء", 27.1),
    ("2380", "بترو رابغ", 7.8),
]


def write_csv(name: str, header: list, rows: list) -> None:
    """الكتابة بترميز UTF-8 مع BOM حتى يفتحها Excel بالعربية بشكل صحيح."""
    with open(DATA_DIR / name, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def yes_no(prob: float, positive="صاعد", negative="هابط") -> str:
    r = random.random()
    if r < prob:
        return positive
    if r < prob * 1.6:
        return negative
    return ""


def generate() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    now_str = now.strftime("%H:%M:%S")

    sahmak, liquidity, moving, aroon, breakout, rsi, ichimoku, ticks = ([] for _ in range(8))

    for sym, name, base in STOCKS:
        # "مزاج" عشوائي لكل سهم: موجب = ضغط شراء ، سالب = ضغط بيع
        mood = random.uniform(-1, 1)
        price = round(base * (1 + mood * 0.02 + random.uniform(-0.005, 0.005)), 2)
        change = round((price / base - 1) * 100, 2)

        buy_ratio = min(max(0.5 + mood * 0.4 + random.uniform(-0.08, 0.08), 0.05), 0.95)
        value = random.uniform(5e6, 120e6)
        buy_val, sell_val = value * buy_ratio, value * (1 - buy_ratio)
        volume = int(value / price)
        buy_vol, sell_vol = int(volume * buy_ratio), volume - int(volume * buy_ratio)
        trades = random.randint(800, 9000)
        buy_tr = int(trades * buy_ratio)

        sahmak.append([now_str, sym, name, price, change, trades, buy_tr, trades - buy_tr,
                       volume, buy_vol, sell_vol, round(value), round(buy_val), round(sell_val)])

        # السيولة اللحظية: آخر 5 فترات (كل 5 دقائق) لكل سهم
        for k in range(5, 0, -1):
            t = (now - timedelta(minutes=5 * (k - 1))).strftime("%H:%M:%S")
            f = random.uniform(0.6, 1.4) / 5
            bv, sv = buy_val * f, sell_val * f * random.uniform(0.8, 1.2)
            liquidity.append([t, sym, name, int(bv / price), round(bv), int(buy_tr * f),
                              int(sv / price), round(sv), int((trades - buy_tr) * f),
                              round(bv - sv), round(bv / (bv + sv) * 100, 2)])

        # المتوسطات: السعر فوق/تحت المتوسطات حسب المزاج
        mas = [round(price * (1 - mood * 0.01 * p + random.uniform(-0.004, 0.004)), 2)
               for p in (0.3, 0.6, 1.0, 1.6, 2.5, 3.5, 5.0)]
        pos = sum(price > m for m in mas)
        moving.append([sym, name, *mas, pos, 7 - pos])

        # Aroon و MACD
        up = round(min(max(50 + mood * 50 + random.uniform(-15, 15), 0), 100))
        down = round(min(max(50 - mood * 50 + random.uniform(-15, 15), 0), 100))
        macd = round(mood * base * 0.01 + random.uniform(-0.05, 0.05), 3)
        sig = round(macd - mood * base * 0.003 + random.uniform(-0.03, 0.03), 3)
        cmf = round(mood * 0.25 + random.uniform(-0.05, 0.05), 3)
        aroon.append([sym, name, up, down, up - down, macd, sig, round(macd - sig, 3),
                      round(price * (1 - mood * 0.03), 2), cmf, round(mood * 1.2, 3),
                      round(change, 2), round(mood * 0.8, 3)])

        # الاختراقات
        p = 0.15 + max(mood, 0) * 0.6
        breakout.append([sym, name, yes_no(p), yes_no(p * 0.6), yes_no(p * 0.5), yes_no(p * 0.7),
                         yes_no(0.2), yes_no(0.2), yes_no(0.15), yes_no(0.1, "نعم", ""),
                         yes_no(p * 0.4), yes_no(p * 0.5), yes_no(p * 0.5), yes_no(0.1),
                         yes_no(0.05, "فجوة صاعدة", "فجوة هابطة")])

        # RSI و Stochastic
        r = round(min(max(50 + mood * 30 + random.uniform(-10, 10), 2), 98), 2)
        k = round(min(max(50 + mood * 45 + random.uniform(-10, 10), 0), 100), 2)
        rsi.append([sym, name, r, round(k - random.uniform(0, 8), 2), k,
                    round(min(max(k / 100 + random.uniform(-0.1, 0.1), 0), 1), 2),
                    round(mood * 150, 1), round(min(max(r + random.uniform(-8, 8), 0), 100), 1),
                    round(k - 100, 1)])

        # الغيمة اليابانية
        above = mood > 0.2
        ichimoku.append([sym, name, "إيجابية" if mood > 0 else "سلبية",
                         "فوق الغيمة" if above else ("داخل الغيمة" if mood > -0.2 else "تحت الغيمة"),
                         "فوق" if mood > 0 else "تحت", "فوق" if mood > 0.1 else "تحت"])

        # الصفقات اللحظية: آخر 40 صفقة لكل سهم
        tp = price
        for j in range(40, 0, -1):
            t = (now - timedelta(seconds=j * random.randint(5, 20))).strftime("%H:%M:%S")
            direction = 1 if random.random() < buy_ratio else -1
            if random.random() < 0.15:
                direction = 0
            tp = round(max(tp + direction * base * 0.0005 * random.random(), 0.01), 2)
            size = random.choice([random.randint(100, 5000)] * 9 + [random.randint(50001, 250000)])
            ticks.append([t, sym, size, tp, round(size * tp), direction, random.randint(1, 15)])

    write_csv("sahmak.csv", ["الوقت", "الرمز", "الاسم", "السعر", "التغير %", "الصفقات",
                             "صفقات الشراء", "صفقات البيع", "الحجم", "حجم الشراء", "حجم البيع",
                             "القيمة", "قيمة الشراء", "قيمة البيع"], sahmak)
    write_csv("liquidity.csv", ["الوقت", "الرمز", "الاسم", "حجم الشراء", "قيمة الشراء", "صفقات الشراء",
                                "حجم البيع", "قيمة البيع", "صفقات البيع", "صافي السيولة",
                                "نسبة السيولة %"], liquidity)
    write_csv("moving_avg.csv", ["الرمز", "الاسم", "المتوسط 3", "المتوسط 5", "المتوسط 10", "المتوسط 20",
                                 "المتوسط 50", "المتوسط 100", "المتوسط 200", "إشارات إيجابية",
                                 "إشارات سلبية"], moving)
    write_csv("aroon_macd.csv", ["الرمز", "الاسم", "Aroon Up", "Aroon Down", "Aroon Oscillator", "MACD",
                                 "MACD Signal", "MACD Histogram", "Parabolic SAR", "CMF", "PPO", "ROC",
                                 "EOM"], aroon)
    write_csv("breakout.csv", ["الرمز", "الاسم", "اختراق متوسط 10", "تقاطع متوسط 5 مع 20",
                               "اختراق MACD للصفر", "تقاطع MACD", "تقاطع Stochastic", "تقاطع Aroon",
                               "تقاطع ADX", "غيمة جدية", "اختراق الغيمة", "اختراق Kijun",
                               "تقاطع Tenken مع Kijun", "انعكاس Parabolic SAR", "فجوة سعرية"], breakout)
    write_csv("rsi_stoch.csv", ["الرمز", "الاسم", "RSI", "Stochastic D", "Stochastic K", "StochRSI",
                                "CCI", "MFI", "W%R"], rsi)
    write_csv("ichimoku.csv", ["الرمز", "الاسم", "الغيمة", "السعر مع الغيمة", "السعر مع Kijun Sen",
                               "Tenken Sen مع Kijun Sen"], ichimoku)
    # علامة تخبر البوت ولوحة الويب أن هذه البيانات تجريبية (تُحذف تلقائيًا عند إدخال بيانات حقيقية)
    (DATA_DIR / ".sample_data").write_text("بيانات تجريبية من sample_data.py", encoding="utf-8")
    write_csv("ticks.csv", ["الوقت", "الرمز", "حجم آخر", "السعر", "القيمة", "الاتجاه", "العدد"], ticks)
    print(f"✅ [{now_str}] تم إنشاء البيانات التجريبية في {DATA_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="توليد بيانات تجريبية")
    parser.add_argument("--loop", type=int, default=0, help="إعادة التوليد كل N ثانية")
    args = parser.parse_args()
    generate()
    while args.loop > 0:
        time.sleep(args.loop)
        generate()
