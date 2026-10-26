// «Opening Candle Indicator Final» منقول من Pine Script بنفس المدخلات والقيم الافتراضية:
// شمعة الافتتاح بلون مميز، خطا قمتها وقاعها وخمسة أهداف فوق وتحت (2، 4، 6، 8، 10 × مداها)،
// VWAP الجلسة، EMA، تظليل منطقة الافتتاح، وإشارات Buy/Sell على الشموع المكتملة فقط.
// أوقات الشموع في المنصة مزاحة لتوقيت الرياض (GMT+3) مثل timestamp("GMT+3", ...) في الأصل.
(function () {
  const PINE = { red: "#FF5252", green: "#4CAF50", white: "#FFFFFF", orange: "#FF9800", fuchsia: "#E040FB", gray: "#787B86" };
  const DEF = {
    market: "السوق السعودي",
    customStartHour: 9, customStartMinute: 0, customEndHour: 16, customEndMinute: 0,
    barDuration: 300000,
    autoDuration: true,  // إضافة: مدة شمعة الافتتاح = فاصل الشارت (بدل 5 دقائق ثابتة)
    lineHighColor: PINE.red, lineHighWidth: 2, lineLowColor: PINE.green, lineLowWidth: 2,
    targetLineColor: PINE.white, targetLineWidth: 1,
    openCandleColor: PINE.white,
    showVWAP: true, vwapColor: PINE.orange, vwapWidth: 2,
    showSignals: true, buyColor: PINE.green, exitColor: PINE.red,
    length: 200, emaColor: PINE.fuchsia,
    showOpeningZone: true, zoneColor: PINE.gray,  // الشفافية 85 كما في الأصل
    breakAlert: false,  // بديل alertcondition في TradingView: إشعار المتصفح
  };
  const US_AUTO = "السوق الأمريكي (تلقائي صيفي/شتوي)";
  const MARKETS = ["السوق السعودي", "السوق الأمريكي", US_AUTO, "مخصص"];

  // فرق توقيت نيويورك عن UTC في يوم معيّن (−4 صيفي، −5 شتوي) من قاعدة المناطق الزمنية في المتصفح
  const nyOffsetCache = new Map();
  function nyOffset(d) {
    if (nyOffsetCache.has(d)) return nyOffsetCache.get(d);
    let off = -4;
    try {
      const t = new Date((d * 86400 + 12 * 3600 - 3 * 3600) * 1000);  // ظهر ذلك اليوم (أوقات الشموع مزاحة +3)
      const part = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", timeZoneName: "shortOffset" })
        .formatToParts(t).find(x => x.type === "timeZoneName").value;  // "GMT-4"
      const m = part.match(/GMT([+-]\d+)/); if (m) off = +m[1];
    } catch {}
    nyOffsetCache.set(d, off);
    return off;
  }

  function sessionTimes(cfg, d) {
    if (cfg.market === US_AUTO) {  // 9:30–16:00 نيويورك بتوقيت الرياض: 16:30–23:00 صيفًا و 17:30–00:00 شتاءً
      const shift = (3 - nyOffset(d)) * 3600;
      return { start: 9.5 * 3600 + shift, end: 16 * 3600 + shift };
    }
    const s = cfg.market === "السوق السعودي" ? [10, 0, 15, 0] : cfg.market === "السوق الأمريكي" ? [16, 30, 23, 0]
      : [cfg.customStartHour, cfg.customStartMinute, cfg.customEndHour, cfg.customEndMinute];
    return { start: s[0] * 3600 + s[1] * 60, end: s[2] * 3600 + s[3] * 60 };
  }

  // ta.ema: البذرة متوسط بسيط لأول length قيمة
  function ema(bars, n) {
    const out = new Array(bars.length).fill(null), k = 2 / (n + 1);
    let prev = null, sum = 0;
    bars.forEach((b, i) => {
      if (prev == null) { sum += b.close; if (i === n - 1) { prev = sum / n; out[i] = prev; } }
      else { prev = b.close * k + prev * (1 - k); out[i] = prev; }
    });
    return out;
  }

  // dayOf: رقم يوم التداول للشمعة (مثل time("D") لبورصة السهم)، nowS: الآن بنفس إزاحة أوقات الشموع
  function compute(bars, cfg, dayOf, nowS, step) {
    const n = bars.length, dur = cfg.autoDuration ? step : cfg.barDuration / 1000;
    const E = ema(bars, cfg.length), V = new Array(n).fill(null), first = new Array(n).fill(false);
    const markers = [], boxes = [];
    let levels = null, tracked = -1, alertAt = null;
    let oh = null, ol = null, rng = null, pv = null, vv = null, vwap = null, state = 0, lastSellHigh = null;
    for (let i = 0; i < n; i++) {
      const b = bars[i], d = dayOf(b.time);
      const { start, end } = sessionTimes(cfg, d), ss = d * 86400 + start, se = d * 86400 + end;
      const isFirst = b.time >= ss && b.time < ss + dur;
      first[i] = isFirst;
      if (i > 0 && dayOf(bars[i - 1].time) !== d) {  // ta.change(time("D"))
        oh = ol = rng = pv = vv = vwap = null; state = 0; lastSellHigh = null;
        if (tracked >= 0) { boxes.splice(tracked, 1); tracked = -1; }  // box.delete(openZoneBox)
      }
      if (b.time >= ss && b.time <= se) {
        const hlc3 = (b.high + b.low + b.close) / 3;
        pv = pv == null ? hlc3 * b.volume : pv + hlc3 * b.volume;
        vv = vv == null ? b.volume : vv + b.volume;
        vwap = pv / vv;  // 0/0 = NaN مثل na
        if (!isFinite(vwap)) vwap = null;
      }
      V[i] = vwap;
      if (isFirst) {
        oh = b.high; ol = b.low; rng = b.high - b.low;
        levels = { t1: ss, t2: se - dur, high: oh, low: ol, up: [2, 4, 6, 8, 10].map(k => oh + k * rng), dn: [2, 4, 6, 8, 10].map(k => ol - k * rng) };
        if (cfg.showOpeningZone) { boxes.push({ left: i, right: i + 1, top: oh, bottom: ol }); tracked = boxes.length - 1; }
      }
      if (tracked >= 0) boxes[tracked].right = i;
      // barstate.isconfirmed: آخر شمعة لم تكتمل بعد لا تُقيَّم
      const confirmed = i < n - 1 || b.time + step <= nowS;
      const gt = (a, c) => a != null && c != null && a > c, lt = (a, c) => a != null && c != null && a < c;
      if (cfg.showSignals && confirmed) {
        if (state === -1 && gt(b.close, lastSellHigh) && gt(b.close, vwap)) {
          markers.push({ time: b.time, position: "belowBar", shape: "arrowUp", text: "Buy", color: cfg.buyColor }); state = 1;
        } else if (state === 0 && !isFirst && gt(b.close, oh) && gt(b.close, vwap)) {
          markers.push({ time: b.time, position: "belowBar", shape: "arrowUp", text: "Buy", color: cfg.buyColor }); state = 1;
        } else if (state === 1 && !isFirst && lt(b.close, vwap) && i > 0 && gt(bars[i - 1].close, oh)) {
          markers.push({ time: b.time, position: "aboveBar", shape: "arrowDown", text: "Sell", color: cfg.exitColor });
          lastSellHigh = b.high; state = -1;
        }
      }
      // openBreakAlert (alertcondition في الأصل)
      if (!isFirst && i > 0 && lt(bars[i - 1].close, oh) && gt(b.close, oh) && gt(b.close, vwap)) alertAt = b.time;
    }
    return { ema: E, vwap: V, first, markers, boxes, levels, alertAt };
  }

  window.initOpeningCandle = function ({ chart, series, container, getBars, dayOf, nowS, step, defaults }) {
    let cfg = { ...DEF, ...(defaults || {}) }, visible = false, res = null, sig = "", alerted = null;
    const base = { ...cfg };
    try {
      const saved = JSON.parse(localStorage.getItem("oc_cfg")) || {};
      // إعدادات محفوظة قبل إضافة الخيار التلقائي: «السوق الأمريكي» كان الافتراضي لا اختيارًا، فيصبح التلقائي
      if (saved.market === "السوق الأمريكي" && !("autoDuration" in saved)) saved.market = US_AUTO;
      cfg = { ...cfg, ...saved };
    } catch {}
    const emaLine = chart.addLineSeries({ priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, visible: false });
    const vwapLine = chart.addLineSeries({ priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, visible: false });

    const canvas = document.createElement("canvas");
    canvas.style.cssText = "position:absolute;inset:0;z-index:2;pointer-events:none";
    container.appendChild(canvas);
    const ctx = canvas.getContext("2d");

    const alpha = (hex, a) => { const v = parseInt(hex.slice(1), 16); return `rgba(${v >> 16},${(v >> 8) & 255},${v & 255},${a})`; };
    function timeToX(t) {  // مثل أدوات الرسم: وقت خارج الشموع يُقدَّر بطول الشمعة
      const b = getBars();
      if (!b.length) return null;
      const s = step();
      let lo = 0, hi = b.length - 1, logical;
      if (t <= b[0].time) logical = (t - b[0].time) / s;
      else if (t >= b[hi].time) logical = hi + (t - b[hi].time) / s;
      else {
        while (hi - lo > 1) { const m = (lo + hi) >> 1; b[m].time <= t ? lo = m : hi = m; }
        logical = lo + (t - b[lo].time) / (b[hi].time - b[lo].time);
      }
      return chart.timeScale().logicalToCoordinate(logical);
    }

    function recalc(force) {
      const b = getBars(), last = b[b.length - 1];
      const s = last ? `${b.length}|${b[0].time}|${last.time}|${last.high}|${last.low}|${last.close}|${last.volume}|${step()}|${JSON.stringify(cfg)}` : "";
      if (s === sig && !force) return false;
      sig = s;
      res = visible && b.length ? compute(b, cfg, dayOf, nowS(), step()) : null;
      // مثل plot مع na في Pine: فجوة في الخط. الشارت يصل الخط عبر النقاط الفارغة، فنملأ الفجوة بنقاط شفافة
      const pts = (arr) => {
        if (!res) return [];
        const first = arr.findIndex(v => v != null);
        if (first < 0) return b.map(x => ({ time: x.time }));
        let last = arr[first];
        return b.map((x, i) => {
          if (i < first) return { time: x.time };
          if (arr[i] == null) return { time: x.time, value: last, color: "rgba(0,0,0,0)" };
          last = arr[i];
          return i > first && arr[i - 1] == null ? { time: x.time, value: arr[i], color: "rgba(0,0,0,0)" } : { time: x.time, value: arr[i] };
        });
      };
      emaLine.applyOptions({ visible: visible, color: cfg.emaColor, lineWidth: 2 });
      vwapLine.applyOptions({ visible: visible && cfg.showVWAP, color: cfg.vwapColor, lineWidth: cfg.vwapWidth });
      emaLine.setData(pts(res?.ema || [])); vwapLine.setData(pts(res?.vwap || []));
      // تنبيه اختراق قمة الافتتاح: مرة لكل شمعة، وللشمعة الأخيرة فقط
      if (res && cfg.breakAlert && res.alertAt != null && res.alertAt === last.time && alerted !== last.time) {
        alerted = last.time;
        try { if ("Notification" in window && Notification.permission === "granted") new Notification("اختراق قمة شمعة الافتتاح", { body: "الشمعة السابقة تحت القمة، الحالية اخترقتها وفوق VWAP" }); } catch {}
      }
      return true;
    }

    function resize() {
      const dpr = window.devicePixelRatio || 1, r = container.getBoundingClientRect();
      if (canvas.width !== Math.round(r.width * dpr) || canvas.height !== Math.round(r.height * dpr)) {
        canvas.width = Math.round(r.width * dpr); canvas.height = Math.round(r.height * dpr);
        canvas.style.width = r.width + "px"; canvas.style.height = r.height + "px";
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return r;
    }
    function hline(t1, t2, p, color, w) {
      const x1 = timeToX(t1), x2 = timeToX(t2), y = series.priceToCoordinate(p);
      if (x1 == null || x2 == null || y == null) return;
      ctx.strokeStyle = color; ctx.lineWidth = w; ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.stroke();
    }
    function render() {
      const r = resize();
      ctx.clearRect(0, 0, r.width, r.height);
      if (visible && res) {
        const ts = chart.timeScale();
        for (const z of res.boxes) {
          const x1 = ts.logicalToCoordinate(z.left), x2 = ts.logicalToCoordinate(z.right), y1 = series.priceToCoordinate(z.top), y2 = series.priceToCoordinate(z.bottom);
          if ([x1, x2, y1, y2].some(v => v == null)) continue;
          ctx.fillStyle = alpha(cfg.zoneColor, 0.15); ctx.fillRect(x1, Math.min(y1, y2), x2 - x1, Math.abs(y2 - y1));
        }
        const L = res.levels;
        if (L) {
          L.up.concat(L.dn).forEach(p => hline(L.t1, L.t2, p, cfg.targetLineColor, cfg.targetLineWidth));
          hline(L.t1, L.t2, L.high, cfg.lineHighColor, cfg.lineHighWidth);
          hline(L.t1, L.t2, L.low, cfg.lineLowColor, cfg.lineLowWidth);
        }
      }
      requestAnimationFrame(render);
    }
    requestAnimationFrame(render);

    return {
      DEF: base, MARKETS, US_AUTO,
      get config() { return { ...cfg }; },
      get state() { return res; },
      get visible() { return visible; },
      recalc,
      setConfig(c) { cfg = { ...cfg, ...c }; try { localStorage.setItem("oc_cfg", JSON.stringify(cfg)); } catch {} recalc(true); },
      setVisible(v) { if (v === visible) return; visible = v; recalc(true); },
      barColor: i => visible && res && res.first[i] ? cfg.openCandleColor : null,
      markers: () => visible && res ? res.markers : [],
    };
  };
})();
