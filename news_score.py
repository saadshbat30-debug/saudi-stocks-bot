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
    (r"\bfda\b.{0,40}\b(approv|clear)(?!\w*[- ](phase|trial|study|ind\b|clinical|protocol))|(approv|clear)\w* by (the )?fda|receives? fda (approval|clearance)|510\(k\)", 45, "موافقة FDA"),
    (r"(fda|agency) (lifts?|lifted|removes?|removed|releases?) .{0,20}(clinical )?hold|clinical hold (lifted|removed)", 35, "رفع التعليق السريري"),
    (r"\bnon-?dilutive\b", 10, "تمويل بلا تخفيف"),
    (r"breakthrough (therapy|device)|fast track|orphan drug|priority review|rare pediatric", 25, "تصنيف FDA مميز (مسار سريع/دواء نادر)"),
    (r"(positive|successful|encouraging|statistically significant)\s.{0,60}(results?|data|topline|trial)|met (its |the )?primary endpoint", 40, "نتائج تجارب إيجابية"),
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
    (r"\b(to |will )?present\w* .{0,40}\b(data|results)\b|late-breaking", 12, "عرض بيانات تجارب قادم"),
    (r"medicare (reimbursement|coverage|payment)|reimbursement (approval|coverage)|\bcms\b .{0,20}(approv|cover|code)|(secures?|receives?|grant(ed|s)?) .{0,20}(reimbursement|coverage)", 25, "تغطية تأمينية / تعويض Medicare"),
    (r"deleverag|debt[- ](paydown|reduction|repayment|free)|pays? (down|off) .{0,20}debt|refinanc", 12, "خفض الديون / إعادة تمويل"),
    (r"completion of acquisition", 10, "إتمام استحواذ (8-K)"),
    (r"changes? in control of registrant", 10, "تغيّر السيطرة على الشركة (8-K)"),
    (r"expan(ds|sion)|enters? .{0,20}market|opens? new", 8, "توسع"),
    # ===== سلبي =====
    (r"chapter 11|chapter 7|bankrupt|insolven|receivership|wind(s|ing)? down", -70, "إفلاس / تصفية"),
    (r"\bdelist|delisting|suspension of trading|form 25\b|(move|transfer)s? to (the )?otc", -45, "شطب من البورصة"),
    (r"(nasdaq|nyse)( american)? (notice|notification|deficiency|letter)|minimum bid|non-?compliance|listing (rule|requirement|standard)", -25, "إنذار من البورصة"),
    (r"reverse (stock |share )?split", -30, "تقسيم عكسي"),
    (r"(public|registered direct|direct|private placement|at-the-market|\batm\b|underwritten|secondary|follow-on) offering|prices? .{0,40}offering|warrant (inducement|exercise)|shelf registration|(?<!non-)(?<!non)\bdilut", -35, "طرح أسهم جديدة (تخفيف)"),
    (r"going concern|substantial doubt", -35, "شك في استمرار الشركة"),
    (r"complete response letter|\bcrl\b|fda (rejects|declines|refuses)|(?<!lifts )(?<!lifted )(?<!removes )(?<!removed )clinical hold(?! (lifted|removed))|fail(s|ed)? to (meet|achieve)|did not meet|missed (the )?primary endpoint|discontinu(es|ed) (the )?(trial|program|development)", -50, "فشل/رفض في FDA أو التجارب"),
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

# قوائم كلمات مالية على طريقة Loughran-McDonald: كلمات مثل cost و liability و tax ليست سلبية في السياق المالي،
# مع فئتين إضافيتين: الغموض (يخفض الثقة) والتقاضي. جذور كلمات تشمل كل التصريفات.
POS_WORDS = (r"surg|soar|jump|rall(y|ies|ied)|skyrocket|spik|record|strong|robust|growth|grow|milestone|breakthrough|wins?|won|gain|climb|rocket|"
             r"outperform|bullish|beat|accelerat|exceed|surpass|achiev|improv|profitab|boost|expand|upgrad|success|favorabl|strength|"
             r"momentum|outstanding|excellent|impressive|advanc|attain|enhanc|optimis|confiden|rebound|recover|upside|innovat|lead(s|ing)? the")
NEG_WORDS = (r"plung|tumbl|slump|sink|sank|crash|fall|fell|drop|weak|loss|losses|declin|slid|warn|concern|bearish|cut|underperform|"
             r"trouble|struggl|halt|probe|miss|deteriorat|impair|default|delinquen|downgrad|shortfall|adverse|unfavorabl|"
             r"disappoint|failure|fail|terminat|suspend|delay|penalt|violat|breach|layoff|restructur|writedown|write-down|"
             r"downturn|slowdown|headwind|downside|plummet|collaps|turmoil|crisis|bankrupt|insolven|dilut|worsen|lower(s|ed)? (guidance|outlook)")
UNCERTAIN_WORDS = (r"uncertain|might|could|possibl|perhaps|approximat|anticipat|believ|expect|tentativ|preliminar|"
                   r"unclear|unknown|unpredictab|volatil|fluctuat|depend|assum|estimat|speculat|risk|exposure|contingen|pending|potential")
LITIGIOUS_WORDS = r"lawsuit|litigat|plaintiff|defendant|indict|subpoena|settlement|class action|alleg(e|ed|es|ing|ation|ations|edly)\b|sued\b|sues\b|courts?\b|legal proceeding|arbitrat|injunction"
_POS = re.compile(rf"\b({POS_WORDS})\w*", re.I)
_NEG = re.compile(rf"\b({NEG_WORDS})\w*", re.I)
_UNC = re.compile(rf"\b({UNCERTAIN_WORDS})\w*|\bmay\b(?! \d)", re.I)
_MAY = re.compile(r"(?<![A-Z][a-z]{2} )\bmay\b(?! \d)")  # "may" بمعنى ربما وليس شهر May (حساس لحالة الأحرف)
_LIT = re.compile(rf"\b({LITIGIOUS_WORDS})", re.I)
# الشركة هي المدعية أو كسبت القضية: التقاضي هنا ليس سلبيًا عليها
PLAINTIFF = re.compile(r"\b(sues|suing|files? (a )?(patent )?(lawsuit|suit|complaint|claim) against|brings? (a )?(lawsuit|suit|action) against|takes? legal action against)\b", re.I)
LIT_WIN = re.compile(r"\b(wins?|won|prevails?|prevailed)\b.{0,30}\b(lawsuit|case|verdict|ruling|appeal|litigation|arbitration|patent)|"
                     r"(favorable|favourable) (ruling|verdict|decision)|awarded .{0,20}(damages|\$)|(court|judge|jury) (rules|ruled|sides) (in favor|for)|dismiss(es|ed)? (the )?(lawsuit|case|claims?) against", re.I)
_NEGATION = re.compile(r"\b(not|no|fails? to|failed to|without|lack of|despite)\s+(\w+\s+){0,2}$", re.I)

# بيانات رسمية من الشركة (وكالات نشر البيانات الصحفية)
WIRE = re.compile(r"globenewswire|pr ?newswire|business ?wire|accesswire|newsfile|access newswire|ein presswire", re.I)
# مقالات رأي أو تخمين: أثرها على السعر أضعف عادةً
SPECULATIVE = re.compile(r"\?$|should you|is it time|could (soar|double|be)|might|may be|here'?s why|reasons? to|\bbuy (now|before)\b|millionaire|forever|vs\.?\s|better buy|prediction|top \d+ |stocks? to (buy|watch)", re.I)
# مقالات تشرح حركة حصلت ("لماذا قفز السهم اليوم") تؤكد الحدث لكنها ليست الحدث نفسه
EXPLAINER = re.compile(r"^why\b|stock (is|was) (soaring|jumping|falling|sinking|up|down)|shares? (are|were) (soaring|jumping|falling|sinking)", re.I)


_LM = {"loaded": False, "pos": set(), "neg": set(), "unc": set(), "lit": set(), "file": None, "words": 0}


def _lm():
    """يحمّل قاموس Loughran-McDonald الأصلي إن وُضع ملفه في مجلد data (أي ملف LoughranMcDonald*.csv)."""
    if _LM["loaded"]:
        return _LM if _LM["words"] else None
    _LM["loaded"] = True
    import csv, glob, os
    files = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "LoughranMcDonald*.csv")))
    if not files:
        return None
    try:
        with open(files[-1], encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                w = (row.get("Word") or "").strip().lower()
                if not w:
                    continue
                _LM["words"] += 1
                for col, key in (("Positive", "pos"), ("Negative", "neg"), ("Uncertainty", "unc"), ("Litigious", "lit")):
                    if (row.get(col) or "0").strip() not in ("", "0"):  # القيمة = سنة الإضافة، وصفر = ليست من الفئة
                        _LM[key].add(w)
        _LM["file"] = os.path.basename(files[-1])
        print(f"📚 قاموس Loughran-McDonald: {len(_LM['pos'])} إيجابية · {len(_LM['neg'])} سلبية · {len(_LM['unc'])} غموض · {len(_LM['lit'])} تقاضٍ ({_LM['file']})")
    except (OSError, ValueError) as e:
        print(f"⚠️ تعذر قراءة قاموس Loughran-McDonald: {e}")
        _LM["words"] = 0
    return _LM if _LM["words"] else None


def lm_counts(text):
    """عدد كلمات كل فئة في النص حسب القاموس الأصلي (مطابقة كلمة كاملة)، أو None إن لم يوجد القاموس."""
    d = _lm()
    if not d:
        return None
    toks = re.findall(r"[a-z]+(?:'[a-z]+)?", (text or "").lower())
    out = {"pos": 0, "neg": 0, "unc": 0, "lit": 0}
    for i, w in enumerate(toks):
        for k in out:
            if w in d[k]:
                # النفي قبل كلمة إيجابية يقلبها (طريقة Loughran-McDonald: no/not/none/neither/never/nobody قبلها بثلاث كلمات)
                if k == "pos" and any(x in ("no", "not", "none", "neither", "never", "nobody") for x in toks[max(0, i - 3):i]):
                    out["neg"] += 1
                else:
                    out[k] += 1
    return out


def _tone(text):
    """نبرة الكلمات مع مراعاة النفي: كل كلمة ±5 وبحد ±20. تستخدم القاموس الأصلي إن وُجد، وإلا القوائم المضمنة."""
    c = lm_counts(text)
    if c is not None:
        return max(-20, min(20, 5 * (c["pos"] - c["neg"])))
    pts = 0
    for rx, sign in ((_POS, 1), (_NEG, -1)):
        for m in rx.finditer(text):
            before = text[max(0, m.start() - 30):m.start()]
            pts += -sign * 5 if _NEGATION.search(before) else sign * 5
    return max(-20, min(20, pts))


# أخبار روتينية لا تحرك السهم: مواعيد النتائج والمؤتمرات، منح الموظفين، المشاركة في مؤتمرات
ROUTINE = re.compile(r"inducement grants?|announces? .{0,40}(release date|date for|dates for)|schedules? .{0,60}(conference call|earnings (call|release)|webcast)|"
                     r"(to|will) (host|hold|participate|ring)|(to|will) present(?!.{0,50}\b(data|results)\b)|conference call (schedule|and webcast)|earnings release and conference call|"
                     r"annual (general )?meeting|investor (day|conference)|fireside chat|to report .{0,40}results on|"
                     r"earnings call transcript|call transcript|said on its .{0,20}(earnings )?call|earnings snapshot|what to expect (from|ahead)|"
                     r"earnings preview|ahead of (its )?(q[1-4]|earnings)|key metrics", re.I)
# إيقاف أو إلغاء طرح أسهم = خبر إيجابي (لا تخفيف)، ويُلغي قاعدة «طرح أسهم» السلبية
STOP_OFFERING = re.compile(r"(suspen|terminat|withdr[ae]w|cancel|halt|ends?|discontinu)\w*\s+(of\s+)?(its\s+|the\s+)?.{0,40}offering", re.I)


def is_routine(title):
    return bool(ROUTINE.search(title or ""))


# لغة مستقبلية أو احتمالية: الحدث لم يقع بعد ("expected", "plans to submit", "PDUFA date")
HEDGE = re.compile(r"\b(expect(s|ed)?|anticipat\w*|plans? to|planning to|seeks?|seeking|aims? to|intends? to|could|might|potential(ly)?|"
                   r"pdufa|submit(s|ted|ting)?|filing for|files? for|applies for|appl(y|ied) for|rumou?r\w*|report(edly|s say)|in talks|exploring|before|ahead of|awaits?|awaiting)\b", re.I)
# نفي قبل الحدث الإيجابي يلغيه: "not approved", "fails to win", "denies", "no plans"
EVENT_NEG = re.compile(r"\b(not|no|never|fails? to|failed to|did not|does not|won'?t|without|denie[sd]|den(y|ying)|rejects?|rejected|declin(es|ed))\b[\w\s,'-]{0,25}$", re.I)
_mkt = {"chg": None, "at": 0}


def market_change():
    """تغير مؤشر S&P 500 اليوم (لقياس حركة السهم مقارنة بالسوق) — مخزن دقيقة."""
    if time.time() - _mkt["at"] > 60:
        try:
            from market import get_quotes
            _mkt["chg"] = (get_quotes(["^GSPC"]).get("^GSPC") or {}).get("change_pct")
        except Exception:
            pass
        _mkt["at"] = time.time()
    return _mkt["chg"] or 0.0


def _label(d):
    return ("🟢 إيجابي قوي" if d >= 40 else "🟢 إيجابي" if d >= 15 else
            "🔴 سلبي قوي" if d <= -40 else "🔴 سلبي" if d <= -15 else "⚪ محايد")


def score(title, summary="", ctx=None):
    """ثلاث درجات منفصلة حتى لا يخفي أحدها الآخر:
    - الاتجاه (−100..+100): هل الحدث نفسه إيجابي أم سلبي للسهم؟ نوع الحدث + نبرة الكلمات فقط (جمع بحدود، بلا ضرب)
    - الأهمية (0..100): حجم الأثر المتوقع = قوة الحدث × الثقة + حجم الشركة + المصدر
    - استجابة السوق (−100..+100): حركة السهم اليوم مقارنة بمؤشر S&P 500 والسيولة
    والثقة (0..1) منفصلة: بيان رسمي أعلى، مقال رأي أو لغة مستقبلية أقل.
    ctx: change_pct, vol_ratio, market_cap, publisher, time, market_chg — كلها اختيارية."""
    ctx = ctx or {}
    age_h = (time.time() - ctx["time"]) / 3600 if ctx.get("time") else 24
    fresh = 1.0 if age_h <= 2 else max(0.35, math.exp(-age_h / 30))  # الحداثة للترتيب فقط
    if is_routine(title):
        return {"score": 0, "direction": 0, "importance": 0, "reaction": None, "confidence": 1.0, "label": "⚪ محايد",
                "event": "خبر روتيني", "rank": 0, "factors": [{"why": "خبر روتيني (موعد نتائج / مؤتمر / منح موظفين)", "pts": 0, "g": "dir"}]}
    factors = []
    add = lambda why, pts, g: factors.append({"why": why, "pts": pts, "g": g})

    # ===== 1) الاتجاه: نوع الحدث (الوزن الأكبر) =====
    best_pos = best_neg = None
    stop_offer = bool(STOP_OFFERING.search(title))
    if stop_offer:
        best_pos = (25, "إيقاف/إلغاء طرح أسهم (لا تخفيف)")
    hedged = False
    for rx, w, why in EVENTS:
        if stop_offer and why.startswith("طرح أسهم"):
            continue
        m_t = rx.search(title)
        m_s = None if m_t else (rx.search(summary) if summary else None)
        m = m_t or m_s
        if not m:
            continue
        src = title if m_t else summary
        w2 = w if m_t else w * 0.6
        if w2 > 0:
            before = src[max(0, m.start() - 40):m.start()]
            if EVENT_NEG.search(before):  # "not approved"، "fails to win" ← الحدث الإيجابي لم يحصل
                w2, why = -w2 * 0.5, "نفي: " + why + " لم يحصل"
            elif HEDGE.search(src[max(0, m.start() - 40):m.end() + 25]) or _MAY.search(src[max(0, m.start() - 40):m.end() + 25]):
                w2, why, hedged = w2 * 0.4, why + " (متوقع وليس واقعًا)", True
        if w2 > 0 and (best_pos is None or w2 > best_pos[0]):
            best_pos = (w2, why)
        if w2 < 0 and (best_neg is None or w2 < best_neg[0]):
            best_neg = (w2, why)
    # سياق التقاضي: إذا كانت الشركة المدعية أو كسبت القضية فلا يُحسب «دعوى قضائية» عليها
    plaintiff, lit_win = bool(PLAINTIFF.search(title)), bool(LIT_WIN.search(title))
    if (plaintiff or lit_win) and best_neg and best_neg[1] in ("دعوى قضائية", "تحقيق رسمي"):
        best_neg = None
    if lit_win and (best_pos is None or best_pos[0] < 20):
        best_pos = (20, "كسب قضية / حكم لصالح الشركة")
    event_pts = 0.0
    for b in (best_pos, best_neg):
        if b:
            event_pts += b[0]; add(b[1], round(b[0]), "dir")
    event = None
    if best_pos or best_neg:
        event = (best_neg if not best_pos or (best_neg and abs(best_neg[0]) >= best_pos[0]) else best_pos)[1]

    # نبرة الكلمات: تعديل محدود ±15 حتى لا تتفوق على نوع الحدث
    is_8k = (ctx.get("publisher") or "") == "SEC 8-K"
    t = 0 if is_8k else max(-15, min(15, _tone(title) + _tone(summary or "") * 0.5))
    if abs(t) >= 5:
        add("نبرة الكلمات " + ("إيجابية" if t > 0 else "سلبية"), round(t), "dir")
    # كلمات التقاضي: سلبية خفيفة إن لم يكن الحدث نفسه دعوى قضائية
    lmc = lm_counts(title)
    lit = 0 if is_8k else (lmc["lit"] if lmc is not None else len(_LIT.findall(title)))
    if lit and not (event and "دعوى" in event) and not (plaintiff or lit_win):
        t2 = -5 * min(lit, 2); t += t2; add("لغة تقاضٍ ونزاعات", t2, "dir")
    elif plaintiff and not lit_win:
        add("الشركة هي المدعية (ليست متهمة)", 0, "dir")
    direction = max(-100, min(100, round(event_pts + t)))

    # ===== 2) الثقة (0..1): مصدر الخبر ونوعه =====
    pub = ctx.get("publisher") or ""
    conf = 0.75
    if WIRE.search(pub) or WIRE.search(summary or "") or pub == "SEC 8-K":
        conf = 0.95; add("بيان رسمي / إفصاح SEC", "ثقة 0.95", "conf")
    if SPECULATIVE.search(title):
        conf = min(conf, 0.45); add("مقال رأي / تخمين", "ثقة 0.45", "conf")
    elif EXPLAINER.search(title):
        conf = min(conf, 0.7); add("مقال يشرح حركة حصلت", "ثقة 0.7", "conf")
    if hedged:
        conf = min(conf, 0.6); add("لغة مستقبلية (الحدث لم يقع بعد)", "ثقة 0.6", "conf")
    lmc = lm_counts(title)
    unc = lmc["unc"] if lmc is not None else len([m for m in _UNC.finditer(title) if m.group(0).lower() != "may" or _MAY.search(m.group(0))])
    if unc and not hedged:  # كلمات غموض في العنوان: "may"، "preliminary"، "uncertain"
        conf = max(0.4, conf - 0.1 * min(unc, 2)); add(f"لغة غموض ({unc})", f"ثقة −{0.1 * min(unc, 2):.1f}", "conf")

    # ===== 3) الأهمية (0..100): قوة الحدث × الثقة + حجم الشركة =====
    imp = abs(direction) * 1.1 * conf
    cap = ctx.get("market_cap") or 0
    if cap and abs(direction) >= 10:
        size = 15 if cap < 300e6 else 8 if cap < 2e9 else -15 if cap > 200e9 else -5 if cap > 50e9 else 0
        if size:
            imp += size
            add({15: "شركة صغيرة جدًا: الخبر يحركها أكثر", 8: "شركة صغيرة/متوسطة"}.get(size, "شركة كبيرة: أثر الخبر أقل"), f"{size:+d}", "imp")
    importance = max(0, min(100, round(imp)))

    # ===== 4) استجابة السوق (−100..+100): حركة السهم اليوم مقارنة بـ S&P 500 =====
    reaction = None
    chg, rv = ctx.get("change_pct"), ctx.get("vol_ratio") or 0
    if chg is not None:
        mk = ctx["market_chg"] if ctx.get("market_chg") is not None else market_change()
        rel = chg - mk
        r = max(-70, min(70, rel * 3))
        if abs(rel) >= 2 and rv >= 2:
            r += (30 if rv >= 5 else 15) * (1 if rel > 0 else -1)
        reaction = max(-100, min(100, round(r)))
        if abs(direction) >= 15 and abs(reaction) >= 15:
            same = (direction > 0) == (reaction > 0)
            add(("السوق يؤكد الخبر" if same else "السوق يعاكس الخبر") + f" (السهم {chg:+.1f}% مقابل السوق {mk:+.1f}%" +
                (f"، سيولة {rv:.1f}×" if rv >= 2 else "") + ")", reaction, "react")

    return {"score": direction, "direction": direction, "importance": importance, "reaction": reaction,
            "confidence": round(conf, 2), "label": _label(direction), "event": event,
            "rank": round(importance * fresh, 1), "factors": factors}


HALF_LIFE_H = 6  # نصف عمر أثر الخبر في مزاج السهم: بعد 6 ساعات يصبح وزنه النصف


MOOD_PRIOR = 0.3  # وزن «محايد» افتراضي: خبر واحد قديم لا يبقى مزاجه قويًا إلى الأبد


def mood(items, now=None):
    """مزاج الأخبار للسهم (−100..+100) = Σ الاتجاه × الثقة × e^(−λΔt) ÷ (Σ الثقة × e^(−λΔt) + 0.3).
    الحد 0.3 في المقام يسحب المزاج نحو الصفر عندما تكون الأخبار قليلة أو قديمة (المتوسط وحده يلغي أثر التقادم
    لخبر وحيد لأن e^(−λt) تُختصر بين البسط والمقام). يُرجع أيضًا عمر آخر خبر والوزن المتبقي."""
    now = now or time.time()
    lam = math.log(2) / (HALF_LIFE_H * 3600)
    num = den = 0.0
    n, newest = 0, None
    for i in items:
        if i.get("event") == "خبر روتيني":
            continue
        t = i.get("time") or now
        w = (i.get("confidence") or 0.75) * math.exp(-lam * max(0, now - t))
        num += (i.get("direction", i.get("score")) or 0) * w
        den += w; n += 1
        newest = t if newest is None else max(newest, t)
    age_h = round((now - newest) / 3600, 1) if newest else None
    return {"mood": round(num / (den + MOOD_PRIOR)) if n else None, "n": n, "weight": round(den, 2),
            "newest_age_h": age_h, "stale": age_h is None or age_h > 24}
