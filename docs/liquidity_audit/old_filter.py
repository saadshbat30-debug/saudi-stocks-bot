"""نسخة حرفية من فلتر الأحجام كما كان في الإيداع af8eae9 (market.drop_cumulative_volume) — مرجع «قبل» لاختبارات التدقيق."""
RIYADH_OFFSET = 3 * 3600


def drop_cumulative_volume(bars, truth=None):
    """شموع ياهو داخل اليوم تأتي أحيانًا بحجم = الحجم التراكمي لليوم حتى تلك اللحظة بدل حجم الشمعة نفسها، وتتبدل
    بين طلب وآخر (نسخة سليمة ونسخة معطوبة). قِيس 2026-11-11: TSLA فيها 7 شموع دقيقة كل منها 1.01–1.05× مجموع ما
    قبلها (مجموع الدقيقة 99.2M مقابل 16.6M حجم اليوم)، وNVDA أمس 16 شمعة متصاعدة، وفي شموع الساعة تتجمع عدة منها.
    حجم الشمعة المعطوبة غير معروف، فيُصفَّر ويُعلَّم vol_unknown فيخرج من حساب السيولة ولا يُقدَّر بقيمة مصنوعة.

    truth: {تاريخ نيويورك: حجم اليوم الحقيقي}. اليوم الذي لا يتجاوز مجموع شموع جلسته الرسمية حجمه الحقيقي بأكثر من 15%
    (أو لا مرجع له) لا يُمس،
    فلا تُخفى قفزة حجم حقيقية في يوم سليم. في اليوم المعطوب: الشمعة ≥4× وسيط الشموع السليمة قبلها و≥ربع مجموعها،
    ثم إن بقي المجموع فوق الحقيقي تُزال أكبر الشموع الشاذة (≥2× الوسيط و≥ربع ما قبلها) حتى يعود قربه.
    شمعة الافتتاح 9:30 لا تُقارن بما قبلها؛ تُعلَّم فقط إن بقي المجموع بعد التنظيف أكبر من الحقيقي وكانت هي سبب الزيادة."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    by_day = {}
    for b in bars:
        t = datetime.fromtimestamp(b["time"] - RIYADH_OFFSET, ny)
        by_day.setdefault(t.date(), []).append((t, b))
    bad, recent = 0, []  # recent: أحجام آخر شموع الجلسة الرسمية السليمة، تمتد عبر الأيام (الفواصل الكبيرة قليلة الشموع في اليوم)
    is_regular = lambda t: (9, 30) <= (t.hour, t.minute) < (16, 0)
    # بعد الافتتاح مباشرة المجموع السابق هو شمعة الافتتاح وحدها، فدقيقة نشطة عادية تتجاوز ربعه: قبل 9:45 يُشترط
    # أن تقارب الشمعة المجموع كله (بصمة الحجم التراكمي، قِيس NVDA 9:33 = 1.09×)
    share = lambda t: 0.9 if (t.hour, t.minute) < (9, 45) else 0.25
    for d in sorted(by_day):
        items = by_day[d]
        real = (truth or {}).get(d)
        regular = lambda: sum(b["volume"] for t, b in items if is_regular(t))  # الشمعة اليومية للجلسة الرسمية
        if not real or regular() <= 1.15 * real:  # بلا مرجع، أو اليوم سليم: لا يُمس
            recent = (recent + [b["volume"] for t, b in items if is_regular(t) and b["volume"] > 0])[-30:]
            continue
        cum, opening, before = 0, None, {}
        for t, b in items:
            v = b["volume"]
            before[id(b)] = cum
            if (t.hour, t.minute) == (9, 30):
                opening = b
                cum += v
                continue
            typical = sorted(recent)[len(recent) // 2] if len(recent) >= 2 else None
            if cum > 0 and typical and v >= 4 * typical and v >= share(t) * cum:
                b["volume"], b["vol_unknown"] = 0, True
                bad += 1
                continue
            cum += v
            if is_regular(t) and v > 0:
                recent = (recent + [v])[-30:]
        # الفواصل الكبيرة: الحجم المعطوب يختلط بحجم الشمعة الطبيعي فلا يبلغ 4×. اليوم ثبت عطبه، فتُزال أكبر الشموع
        # الشاذة (≥2× الوسيط و≥ربع المجموع قبلها: بصمة الحجم التراكمي) واحدة واحدة حتى يعود المجموع قرب حجمه
        # الحقيقي — لا أكثر من الزيادة المثبتة، ولا تُمس شمعة كبيرة عادية (مرجع اليوم قد يتأخر دقائق عن الحجم الفعلي)
        typical = sorted(recent)[len(recent) // 2] if len(recent) >= 2 else None
        if typical:
            for t, b in sorted(((t, b) for t, b in items if is_regular(t) and (t.hour, t.minute) != (9, 30)
                                and b["volume"] >= 2 * typical and b["volume"] >= share(t) * before.get(id(b), 0) > 0),
                               key=lambda x: -x[1]["volume"]):
                if regular() <= 1.15 * real:
                    break
                b["volume"], b["vol_unknown"] = 0, True
                bad += 1
        if opening and opening["volume"]:
            total = regular()
            # هي سبب الزيادة فعلًا: الزيادة على الحقيقي ≥ نصف حجمها، وبدونها يعود المجموع قرب الحقيقي
            if total > 1.15 * real and total - real >= 0.5 * opening["volume"] and total - opening["volume"] <= 1.15 * real:
                opening["volume"], opening["vol_unknown"] = 0, True
                bad += 1
    return bad
