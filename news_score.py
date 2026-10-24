"""تقييم الأخبار: درجة من -100 (سلبي جدًا) إلى +100 (إيجابي جدًا) لكل خبر، مع أسباب الدرجة.

الدرجة تجمع عدة نواحٍ:
1. نوع الحدث (موافقة FDA، عقد، استحواذ، نتائج، طرح أسهم، تقسيم عكسي، شطب، إفلاس...) — الوزن الأكبر
2. نبرة الكلمات (يقفز، قياسي، يهبط، خسارة...) مع مراعاة النفي ("fails to")
3. مصدر الخبر: بيان رسمي من الشركة أقوى من مقال رأي أو سؤال ("Should you buy?")
4. حجم الشركة: الخبر يحرك الشركة الصغيرة أكثر من العملاقة
5. ردة فعل السوق: هل السعر والسيولة اليوم يؤكدان اتجاه الخبر؟
6. الحداثة: الخبر الجديد أهم (تؤثر على الترتيب وليس على الإشارة)

تقدير آلي بالكلمات وليس توصية؛ اقرأ الخبر قبل أي قرار.
"""
import math
import re
import time

# (النمط، الوزن، الوصف) — الأوزان تقريبية لقوة الأثر المعتاد على السهم
EVENTS = [
    # ===== إيجابي =====
    (r"\bfda\b.{0,40}\b(approv|clear)|(approv|clear)\w* by (the )?fda|receives? fda (approval|clearance)|510\(k\)", 45, "موافقة FDA"),
    (r"breakthrough (therapy|device)|fast track|orphan drug|priority review|rare pediatric", 25, "تصنيف FDA مميز (مسار سريع/دواء نادر)"),
    (r"(positive|successful|encouraging|statistically significant)\s.{0,30}(results?|data|topline|trial)|met (its |the )?primary endpoint", 40, "نتائج تجارب إيجابية"),
    (r"to be acquired|agree[sd]? to be acquired|acquired by|(definitive|merger) agreement .{0,40}\$\d|per share in cash|take[- ]private|buyout offer", 40, "عرض استحواذ على الشركة"),
    (r"\b(wins?|awarded|secures?|lands?|receives?)\b.{0,40}\b(contract|order|award|deal|grant)\b|purchase order", 25, "عقد / طلبية جديدة"),
    (r"partnership|collaborat|strategic (agreement|alliance)|joint venture|teams? up with|licens(e|ing) agreement", 15, "شراكة / تعاون"),
    (r"(earnings|revenues?|sales|eps|profit)( (and|&) (earnings|revenues?|sales|eps))? (beat|top)|beats? (on )?(earnings|revenue|eps)|beats?\b.{0,25}(estimates|expectations|consensus)|tops? (estimates|expectations)|record (revenue|sales|quarter|profit)|revenue (jumps|soars|surges)", 30, "نتائج أفضل من المتوقع"),
    (r"raises?\b.{0,20}(guidance|outlook|forecast)|boosts? (guidance|outlook)", 30, "رفع التوقعات المستقبلية"),
    (r"(net income|profit|earnings) (rises|jumps|grows|increases|up)|swings? to (a )?profit|first (ever )?profit", 15, "نمو الأرباح"),
    (r"buyback|share repurchase|repurchase program", 20, "إعادة شراء أسهم"),
    (r"(raises|increases|hikes) (its )?(quarterly )?dividend|dividend (hiked|raised|increased|boosted)|special dividend", 15, "زيادة التوزيعات"),
    (r"upgrade[sd]?\b|price target (raised|increased|boosted)|raises? (price )?target|initiates? .{0,20}\b(buy|outperform|overweight)", 20, "ترقية من محللين"),
    (r"regain(s|ed)? compliance", 20, "استعاد الامتثال لشروط البورصة"),
    (r"insiders? (buy|bought|purchas)|ceo (buys|bought|purchases)|director (buys|bought)", 15, "شراء من المطلعين"),
    (r"uplist|approved (for|to) list(ing)? on (the )?(nasdaq|nyse)", 20, "الانتقال لبورصة رئيسية"),
    (r"patent (granted|issued|award)|receives? .{0,20}patent", 10, "براءة اختراع"),
    (r"\blaunch(es|ed)?\b|unveil|introduc(es|ed) (new|its)", 8, "إطلاق منتج"),
    (r"entry into a material definitive agreement", 10, "اتفاقية جوهرية (8-K)"),
    (r"completion of acquisition", 10, "إتمام استحواذ (8-K)"),
    (r"changes? in control of registrant", 10, "تغيّر السيطرة على الشركة (8-K)"),
    (r"expan(ds|sion)|enters? .{0,20}market|opens? new", 8, "توسع"),
    # ===== سلبي =====
    (r"chapter 11|chapter 7|bankrupt|insolven|receivership|wind(s|ing)? down", -70, "إفلاس / تصفية"),
    (r"\bdelist|delisting|suspension of trading|form 25\b|(move|transfer)s? to (the )?otc", -45, "شطب من البورصة"),
    (r"(nasdaq|nyse)( american)? (notice|notification|deficiency|letter)|minimum bid|non-?compliance|listing (rule|requirement|standard)", -25, "إنذار من البورصة"),
    (r"reverse (stock |share )?split", -30, "تقسيم عكسي"),
    (r"(public|registered direct|direct|private placement|at-the-market|\batm\b|underwritten|secondary|follow-on) offering|prices? .{0,40}offering|warrant (inducement|exercise)|shelf registration|\bdilut", -35, "طرح أسهم جديدة (تخفيف)"),
    (r"going concern|substantial doubt", -35, "شك في استمرار الشركة"),
    (r"complete response letter|\bcrl\b|fda (rejects|declines|refuses)|clinical hold|fail(s|ed)? to (meet|achieve)|did not meet|missed (the )?primary endpoint|discontinu(es|ed) (the )?(trial|program|development)", -50, "فشل/رفض في FDA أو التجارب"),
    (r"miss(es|ed)?\b.{0,25}(estimates|expectations|consensus)|falls? short of|below (estimates|expectations)", -30, "نتائج أقل من المتوقع"),
    (r"(cuts?|lowers?|slashes?|withdraws?)\b.{0,20}(guidance|outlook|forecast)|warns? (on|of)", -30, "خفض التوقعات / تحذير"),
    (r"(net )?loss (widen|grows|deepen)|revenue (falls|drops|declines|plunges)", -15, "تراجع النتائج"),
    (r"downgrade[sd]?\b|price target (cut|lowered|reduced)|cuts? (price )?target", -20, "تخفيض من محللين"),
    (r"short (seller|report)|hindenburg|muddy waters|fraud", -30, "تقرير بائع على المكشوف / اتهام احتيال"),
    (r"\bsec\b .{0,20}(charges|investigation|subpoena)|investigation by|indict|doj ", -25, "تحقيق رسمي"),
    (r"class action|securities (fraud|litigation)|investor (alert|lawsuit)|shareholder (alert|rights)|lawsuit", -10, "دعوى قضائية"),
    (r"\brecall", -20, "استدعاء منتج"),
    (r"(ceo|cfo|chief executive|chief financial) .{0,20}(resign|steps down|depart|terminated|fired)", -15, "استقالة قيادي"),
    (r"layoffs?|job cuts|restructuring|workforce reduction", -10, "تسريح / إعادة هيكلة"),
    (r"default(s|ed)? on|missed (a )?(debt )?payment|covenant", -40, "تعثر في سداد الديون"),
    (r"impairment|write[- ]?down|restat(e|ement)", -15, "شطب أصول / إعادة قوائم"),
    (r"\bhalt(ed|s)?\b", -8, "إيقاف تداول"),
    (r"triggering events that accelerate|acceleration of (a )?(debt|obligation)", -35, "تسريع سداد دين / تعثر (8-K)"),
    (r"termination of a material definitive agreement", -15, "إلغاء اتفاقية جوهرية (8-K)"),
    (r"changes? in registrant'?s certifying accountant|auditor (resign|dismiss)", -10, "تغيير مدقق الحسابات"),
]
EVENTS = [(re.compile(rx, re.I), w, why) for rx, w, why in EVENTS]

POS_WORDS = r"surge|soar|jump|rall(y|ies)|skyrocket|spike|record|strong|robust|growth|milestone|breakthrough|wins?|gains?|climbs?|rockets?|outperform|bullish|beats?|accelerat|exceed"
NEG_WORDS = r"plunge|tumble|slump|sink|crash|fall|drop|weak|loss|losses|decline|slide|warns?|concern|bearish|cut|underperform|risk|trouble|struggl|halt|probe|lawsuit|miss"
_POS = re.compile(rf"\b({POS_WORDS})\w*", re.I)
_NEG = re.compile(rf"\b({NEG_WORDS})\w*", re.I)
_NEGATION = re.compile(r"\b(not|no|fails? to|failed to|without|lack of|despite)\s+(\w+\s+){0,2}$", re.I)

# بيانات رسمية من الشركة (وكالات نشر البيانات الصحفية)
WIRE = re.compile(r"globenewswire|pr ?newswire|business ?wire|accesswire|newsfile|access newswire|ein presswire", re.I)
# مقالات رأي أو تخمين: أثرها على السعر أضعف عادةً
SPECULATIVE = re.compile(r"\?$|should you|is it time|could (soar|double|be)|might|may be|here'?s why|reasons? to|\bbuy (now|before)\b|millionaire|forever|vs\.?\s|better buy|prediction|top \d+ |stocks? to (buy|watch)", re.I)
# مقالات تشرح حركة حصلت ("لماذا قفز السهم اليوم") تؤكد الحدث لكنها ليست الحدث نفسه
EXPLAINER = re.compile(r"^why\b|stock (is|was) (soaring|jumping|falling|sinking|up|down)|shares? (are|were) (soaring|jumping|falling|sinking)", re.I)


def _tone(text):
    """نبرة الكلمات مع مراعاة النفي: كل كلمة ±5 وبحد ±20."""
    pts = 0
    for rx, sign in ((_POS, 1), (_NEG, -1)):
        for m in rx.finditer(text):
            before = text[max(0, m.start() - 30):m.start()]
            pts += -sign * 5 if _NEGATION.search(before) else sign * 5
    return max(-20, min(20, pts))


# أخبار روتينية لا تحرك السهم: مواعيد النتائج والمؤتمرات، منح الموظفين، المشاركة في مؤتمرات
ROUTINE = re.compile(r"inducement grants?|announces? .{0,40}(release date|date for|dates for)|schedules? .{0,60}(conference call|earnings (call|release)|webcast)|"
                     r"(to|will) (host|hold|present|participate|ring)|conference call (schedule|and webcast)|earnings release and conference call|"
                     r"annual (general )?meeting|investor (day|conference)|fireside chat|to report .{0,40}results on", re.I)


def is_routine(title):
    return bool(ROUTINE.search(title or ""))


def score(title, summary="", ctx=None):
    """ctx: change_pct, vol_ratio, market_cap, publisher, time — كلها اختيارية."""
    ctx = ctx or {}
    if is_routine(title):
        return {"score": 0, "label": "⚪ محايد", "event": "خبر روتيني", "rank": 0,
                "factors": [{"why": "خبر روتيني (موعد نتائج / مؤتمر / منح موظفين)", "pts": 0}]}
    text = f"{title} {summary or ''}"
    factors, raw, event = [], 0.0, None

    # 1) نوع الحدث: أقوى حدث إيجابي + أقوى حدث سلبي (العنوان أهم من الملخص)
    best_pos = best_neg = None
    for rx, w, why in EVENTS:
        hit_t, hit_s = rx.search(title), (rx.search(summary) if summary else None)
        if not (hit_t or hit_s):
            continue
        w2 = w if hit_t else w * 0.6
        if w2 > 0 and (best_pos is None or w2 > best_pos[0]):
            best_pos = (w2, why)
        if w2 < 0 and (best_neg is None or w2 < best_neg[0]):
            best_neg = (w2, why)
    for b in (best_pos, best_neg):
        if b:
            raw += b[0]; factors.append((b[1], round(b[0])))
    if best_pos or best_neg:
        event = (best_neg if not best_pos or (best_neg and abs(best_neg[0]) >= best_pos[0]) else best_pos)[1]

    # 2) نبرة الكلمات
    t = _tone(title) + _tone(summary or "") * 0.5
    if abs(t) >= 5:
        raw += t; factors.append(("نبرة الكلمات " + ("إيجابية" if t > 0 else "سلبية"), round(t)))

    # 3) المصدر ونوع المقال
    pub = ctx.get("publisher") or ""
    mult = 1.0
    if WIRE.search(pub) or WIRE.search(summary or ""):
        mult *= 1.15; factors.append(("بيان رسمي من الشركة", "×1.15"))
    if SPECULATIVE.search(title):
        mult *= 0.55; factors.append(("مقال رأي / تخمين", "×0.55"))
    elif EXPLAINER.search(title):
        mult *= 0.8; factors.append(("مقال يشرح حركة حصلت", "×0.8"))

    # 4) حجم الشركة
    cap = ctx.get("market_cap") or 0
    if cap and cap < 300e6:
        mult *= 1.3; factors.append(("شركة صغيرة: الخبر يحركها أكثر", "×1.3"))
    elif cap > 200e9:
        mult *= 0.6; factors.append(("شركة عملاقة: أثر الخبر أقل", "×0.6"))

    s = raw * mult

    # 5) ردة فعل السوق اليوم: تأكيد أو تعارض
    chg, rv = ctx.get("change_pct"), ctx.get("vol_ratio") or 0
    if chg is not None and abs(s) >= 10 and abs(chg) >= 3:
        same = (s > 0) == (chg > 0)
        adj = min(15, abs(chg) / 2) * (1 if same else -0.6) * (1 if s > 0 else -1)
        if rv >= 2:
            adj *= 1.3
        s += adj
        factors.append((("السعر يؤكد الخبر" if same else "السعر يعاكس الخبر") + f" ({chg:+.1f}%" + (f"، سيولة {rv:.1f}×" if rv >= 2 else "") + ")", round(adj)))

    s = max(-100, min(100, round(s)))
    # 6) الحداثة: للترتيب فقط
    age_h = (time.time() - ctx["time"]) / 3600 if ctx.get("time") else 24
    fresh = 1.0 if age_h <= 2 else max(0.35, math.exp(-age_h / 30))
    label = ("🟢 إيجابي قوي" if s >= 40 else "🟢 إيجابي" if s >= 15 else
             "🔴 سلبي قوي" if s <= -40 else "🔴 سلبي" if s <= -15 else "⚪ محايد")
    return {"score": s, "label": label, "event": event, "rank": round(abs(s) * fresh, 1),
            "factors": [{"why": w, "pts": p} for w, p in factors]}
