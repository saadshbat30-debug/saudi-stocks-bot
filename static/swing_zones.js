// مؤشر «عرض وطلب بن جمعان» منقول من Pine Script بنفس المدخلات والقيم الافتراضية:
// قمة وقاع آخر موجة، منطقة فوق القمة وتحت القاع بعرض نصف ATR(200)، خط الموجة الأخيرة،
// وإسقاط مستقبلي = متوسط/وسيط/منوال نسب آخر N موجات مكتملة، وجدول القوة.
// الفرق الوحيد عن الأصل: الحجم يُقسم داخل/خارج حسب موقع الإغلاق داخل مدى الشمعة (مثل لوحة السيولة)
// بدل عدّ حجم الشمعة الخضراء كله شراء والحمراء كله بيع، ولذلك يُكتب «تقدير».
(function () {
  const DEF = {
    len: 10, offset: 10, dataqty: 5, calcType: "Average",
    position: "Bottom Right", size: "Small", extendR: false,
    colorBull: "#15dd7c", colorBear: "#eb7514",
  };
  const POSITIONS = ["Middle Right", "Top Center", "Top Right", "Middle Left", "Middle Center", "Bottom Left", "Bottom Center", "Bottom Right"];
  const SIZES = { Tiny: 10, Small: 11.5, Normal: 13, Large: 15 };

  // ATR بمتوسط RMA مثل ta.atr: البذرة متوسط بسيط لأول n قيمة
  function atr(bars, n) {
    const out = new Array(bars.length).fill(null);
    let sum = 0, prev = null;
    for (let i = 0; i < bars.length; i++) {
      const b = bars[i], pc = i ? bars[i - 1].close : null;
      const tr = pc == null ? b.high - b.low : Math.max(b.high - b.low, Math.abs(b.high - pc), Math.abs(b.low - pc));
      if (i < n) { sum += tr; if (i === n - 1) { prev = sum / n; out[i] = prev; } }
      else { prev = (prev * (n - 1) + tr) / n; out[i] = prev; }
    }
    return out;
  }

  const avg = a => a.reduce((s, x) => s + x, 0) / a.length;
  const median = a => { const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
  // مثل array.mode في Pine: الأكثر تكرارًا، وعند التساوي الأصغر (فإن لم يتكرر شيء = أصغر قيمة)
  const mode = a => {
    const c = new Map(); a.forEach(x => c.set(x, (c.get(x) || 0) + 1));
    let best = null, bc = 0;
    for (const [v, k] of c) if (k > bc || (k === bc && v < best)) { best = v; bc = k; }
    return best;
  };

  function compute(bars, cfg) {
    const n = bars.length, len = cfg.len;
    if (n < len + 2) return null;
    const A = atr(bars, 200);
    const h = { val: null, indx: null }, l = { val: null, indx: null };
    let direc = false, prevDirec = false, H = [], L = [];
    let upper = null, lower = null, leg = null;
    const pcs = [];
    for (let i = 0; i < n; i++) {
      let hi = -Infinity, lo = Infinity;
      for (let j = Math.max(0, i - len + 1); j <= i; j++) { hi = Math.max(hi, bars[j].high); lo = Math.min(lo, bars[j].low); }
      H[i] = hi; L[i] = lo;
      const b = bars[i];
      if (b.high === H[i]) direc = true;
      if (b.low === L[i]) direc = false;
      if (i > 0 && bars[i - 1].high === H[i - 1] && b.high < H[i]) { h.indx = i - 1; h.val = bars[i - 1].high; }
      if (i > 0 && bars[i - 1].low === L[i - 1] && b.low > L[i]) { l.indx = i - 1; l.val = bars[i - 1].low; }
      if (i > 0 && direc !== prevDirec) {
        const a = A[i] != null ? A[i] * 0.5 : null;
        if (h.val != null && l.val != null) {
          leg = { x1: h.indx, y1: h.val, x2: l.indx, y2: l.val, color: direc ? cfg.colorBear : cfg.colorBull };
          // ATR(200) غير متاح قبل 200 شمعة: لا تُرسم المنطقة (مثل الأصل)
          upper = a != null ? { left: h.indx, top: h.val + a, bottom: h.val } : null;
          lower = a != null ? { left: l.indx, top: l.val, bottom: l.val - a } : null;
          pcs.push(!direc ? (h.val - l.val) / l.val * 100 : (l.val - h.val) / h.val * 100);
        }
      }
      if (pcs.length > cfg.dataqty) pcs.shift();
      prevDirec = direc;
    }
    if (h.val == null || l.val == null) return null;

    // الحجم من بداية الموجة الأخيرة حتى آخر شمعة
    let buy = 0, sell = 0;
    for (let i = Math.min(h.indx, l.indx); i < n; i++) {
      const b = bars[i];
      let share;
      if (b.high > b.low) share = (b.close - b.low) / (b.high - b.low);
      else { const ref = i ? bars[i - 1].close : b.open; share = b.close > ref ? 1 : b.close < ref ? 0 : 0.5; }
      buy += b.volume * share; sell += b.volume * (1 - share);
    }

    const abs = pcs.map(Math.abs);
    const sv = !abs.length ? null : cfg.calcType === "Median" ? median(abs) : cfg.calcType === "Mode" ? mode(abs) : avg(abs);
    let proj = null;
    if (sv != null) {
      proj = !direc
        ? { x1: h.indx, y1: h.val, y2: h.val - h.val * (sv / 100), text: "-" + sv.toFixed(2) + "%" }
        : { x1: l.indx, y1: l.val, y2: l.val + l.val * (sv / 100), text: "+" + sv.toFixed(2) + "%" };
    }
    return { last: n - 1, h: { ...h }, l: { ...l }, upper, lower, leg, proj, buy, sell, samples: abs.length, direc, needAtr: !upper };
  }

  window.initSwingZones = function ({ chart, series, container, getBars }) {
    let cfg = { ...DEF }, visible = false, res = null, sig = "";
    try { cfg = { ...DEF, ...(JSON.parse(localStorage.getItem("bj_cfg")) || {}) }; } catch {}

    const canvas = document.createElement("canvas");
    canvas.style.cssText = "position:absolute;inset:0;z-index:2;pointer-events:none";
    container.appendChild(canvas);
    const ctx = canvas.getContext("2d");
    const dash = document.createElement("div");
    dash.className = "bj-dash"; dash.hidden = true;
    container.appendChild(dash);

    const fmtP = p => p == null ? "—" : p.toFixed(Math.abs(p) < 1 ? 4 : 2);
    const fmtV = v => { const a = Math.abs(v); return a >= 1e9 ? (v / 1e9).toFixed(3) + "B" : a >= 1e6 ? (v / 1e6).toFixed(3) + "M" : a >= 1e3 ? (v / 1e3).toFixed(3) + "K" : v.toFixed(0); };
    const alpha = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; };

    function recalc() {
      const b = getBars(), last = b[b.length - 1];
      const s = last ? `${b.length}|${b[0].time}|${last.time}|${last.high}|${last.low}|${last.close}|${JSON.stringify(cfg)}` : "";
      if (s === sig) return;
      sig = s; res = b.length ? compute(b, cfg) : null;
      renderDash();
    }

    function renderDash() {
      dash.hidden = !visible || !res;
      if (dash.hidden) return;
      const p = cfg.position.toLowerCase();
      dash.style.cssText = `font-size:${SIZES[cfg.size] || 11.5}px;` +
        (p.startsWith("top") ? "top:34px;" : p.startsWith("bottom") ? "bottom:30px;" : "top:50%;transform:translateY(-50%);") +
        (p.endsWith("left") ? "left:52px;" : p.endsWith("right") ? "right:80px;" : "left:50%;translate:-50% 0;");
      if (p.startsWith("middle") && p.endsWith("center")) dash.style.transform = "translate(-50%,-50%)", dash.style.translate = "";
      const tot = res.buy + res.sell, bp = tot ? res.buy / tot * 100 : 0, sp = tot ? res.sell / tot * 100 : 0;
      const [txt, col] = bp > 55 ? ["سيطرة شرائية", cfg.colorBull] : sp > 55 ? ["سيطرة بيعية", cfg.colorBear] : ["متقارب", "#94a3b8"];
      dash.innerHTML = `<table>
        <tr><th>النوع</th><th>الفوليوم</th><th>القوة %</th></tr>
        <tr><td style="background:${cfg.colorBear};color:#fff">قوة البيع</td><td style="color:${cfg.colorBear}">${fmtV(res.sell)}</td><td style="color:${cfg.colorBear}">${sp.toFixed(2)}%</td></tr>
        <tr><td style="background:${cfg.colorBull};color:#fff">قوة الشراء</td><td style="color:${cfg.colorBull}">${fmtV(res.buy)}</td><td style="color:${cfg.colorBull}">${bp.toFixed(2)}%</td></tr>
        <tr><td>التحليل:</td><td colspan="2" style="color:${col};background:${alpha(col.startsWith("#") && col.length === 7 ? col : "#94a3b8", 0.12)}">${txt}</td></tr>
      </table><div class="bj-note">تقدير من موقع الإغلاق داخل كل شمعة منذ بداية الموجة الأخيرة${res.needAtr ? " · المناطق تظهر بعد 200 شمعة (ATR 200)" : ""} · الإسقاط متوسط تاريخي لآخر ${res.samples} موجات وليس هدفًا مضمونًا</div>`;
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

    function box(z, color, text, right, W) {
      const ts = chart.timeScale(), x1 = ts.logicalToCoordinate(z.left), y1 = series.priceToCoordinate(z.top), y2 = series.priceToCoordinate(z.bottom);
      const x2 = cfg.extendR ? W : ts.logicalToCoordinate(right);
      if (x1 == null || x2 == null || y1 == null || y2 == null) return;
      const top = Math.min(y1, y2), h = Math.max(1, Math.abs(y2 - y1));
      ctx.fillStyle = alpha(color, 0.2); ctx.fillRect(x1, top, x2 - x1, h);
      ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.strokeRect(x1, top, x2 - x1, h);
      ctx.font = `${SIZES[cfg.size] || 11.5}px system-ui, sans-serif`; ctx.fillStyle = "#e5e7eb"; ctx.textAlign = "right"; ctx.direction = "ltr";
      const lines = text.split("\n"), lh = (SIZES[cfg.size] || 11.5) + 2, mid = top + h / 2 - (lines.length - 1) * lh / 2 + 4;
      lines.forEach((t, k) => ctx.fillText(t, Math.min(x2, W) - 4, mid + k * lh));
    }

    function render() {
      const r = resize();
      ctx.clearRect(0, 0, r.width, r.height);
      if (visible) {
        recalc();
        if (res) {
          const ts = chart.timeScale(), X = i => ts.logicalToCoordinate(i), Y = p => series.priceToCoordinate(p), right = res.last + cfg.offset;
          ctx.setLineDash([]);
          if (res.upper) box(res.upper, cfg.colorBear, `${fmtP(res.h.val)}🔺\n-${fmtV(res.sell)}🔻`, right, r.width);
          if (res.lower) box(res.lower, cfg.colorBull, `${fmtP(res.l.val)}🔻\n+${fmtV(res.buy)}🔺`, right, r.width);
          if (res.leg) {
            const [a, b, c, d] = [X(res.leg.x1), Y(res.leg.y1), X(res.leg.x2), Y(res.leg.y2)];
            if ([a, b, c, d].every(v => v != null)) { ctx.strokeStyle = res.leg.color; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(a, b); ctx.lineTo(c, d); ctx.stroke(); }
          }
          if (res.proj) {
            const [a, b, c, d] = [X(res.proj.x1), Y(res.proj.y1), X(right), Y(res.proj.y2)];
            if ([a, b, c, d].every(v => v != null)) {
              ctx.strokeStyle = "#e5e7eb"; ctx.lineWidth = 1; ctx.setLineDash([6, 4]);
              ctx.beginPath(); ctx.moveTo(a, b); ctx.lineTo(c, d); ctx.stroke(); ctx.setLineDash([]);
              ctx.font = `${SIZES[cfg.size] || 11.5}px system-ui, sans-serif`; ctx.textAlign = "left";
              const w = ctx.measureText(res.proj.text).width + 10;
              ctx.fillStyle = "rgba(229,231,235,.2)"; ctx.fillRect(c + 4, d - 9, w, 18);
              ctx.fillStyle = "#e5e7eb"; ctx.fillText(res.proj.text, c + 9, d + 4);
            }
          }
        }
      }
      requestAnimationFrame(render);
    }
    requestAnimationFrame(render);

    // مثل TradingView: المناطق والإسقاط رسومات لا تدخل في النطاق التلقائي للسعر (كانت تمدّه لأسعار سالبة مع قاع شاذ)

    const applyOffset = () => chart.timeScale().applyOptions({ rightOffset: visible ? cfg.offset + 6 : 0 });
    return {
      DEF, POSITIONS,
      get config() { return { ...cfg }; },
      get state() { return res; },
      setConfig(c) {
        cfg = { ...cfg, ...c }; sig = "";
        try { localStorage.setItem("bj_cfg", JSON.stringify(cfg)); } catch {}
        applyOffset(); recalc();
      },
      setVisible(v) { if (v === visible) return; visible = v; sig = ""; applyOffset(); recalc(); renderDash(); },
    };
  };
})();
