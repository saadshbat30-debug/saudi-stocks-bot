// ضغط الشراء/البيع التقديري — معادلة واحدة للّوحة والسلّم والمؤشر السفلي (ونظيرتها في الخادم market._flow).
// تقدير من شموع OHLCV وليس تنفيذات مصنفة شراءً وبيعًا:
//   قيمة الشمعة   = (الأعلى + الأدنى + الإغلاق) ÷ 3 × الحجم
//   حصة الشراء    = (الإغلاق − الأدنى) ÷ (الأعلى − الأدنى)
//   شمعة بلا مدى  = حسب اتجاه إغلاقها عن إغلاق الشمعة السابقة في اليوم نفسه (أو عن افتتاحها إن كانت الأولى):
//                   أعلى ← شراء كلها، أدنى ← بيع كلها، مساوٍ ← مناصفة
//   الصافي        = قيمة الشمعة × (2 × حصة الشراء − 1)
(function () {
  // يوم التداول: أوقات الشموع مزاحة لتوقيت الرياض (+3)، والحد 04:00 UTC يفصل ما بعد الإغلاق عن اليوم التالي
  const dayOf = t => Math.floor((t - 7 * 3600) / 86400);

  // prevClose: إغلاق الشمعة السابقة في اليوم نفسه، أو null لأول شمعة في اليوم
  function split(b, prevClose) {
    const volume = b.volume || 0, value = (b.high + b.low + b.close) / 3 * volume;
    let share;
    if (b.high > b.low) share = (b.close - b.low) / (b.high - b.low);
    else { const ref = prevClose == null ? b.open : prevClose; share = b.close > ref ? 1 : b.close < ref ? 0 : 0.5; }
    return { value, buy: value * share, sell: value * (1 - share), net: value * (2 * share - 1) };
  }

  // لكل شمعة: {time, day, value, buy, sell, net, cum} — cum صافي تراكمي يبدأ من جديد كل يوم تداول
  function series(bars) {
    let day = null, cum = 0, prev = null;
    return bars.map(b => {
      const d = dayOf(b.time);
      if (d !== day) { day = d; cum = 0; prev = null; }
      const s = split(b, prev);
      prev = b.close; cum += s.net;
      return { time: b.time, day: d, ...s, cum };
    });
  }

  const api = { dayOf, split, series };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.pressure = api;
})();
