// مؤشر «الموجات» للتعلّم: يقسم الحركة إلى موجات صاعدة وهابطة، كل موجة في مربع،
// ويسمّي القمم والقيعان (قمة أعلى/أدنى، قاع أعلى/أدنى) ويستنتج الاتجاه منها.
// القمة = أعلى سعر بين N شمعة قبلها و N شمعة بعدها (مثل ta.pivothigh)، فلا تتأكد إلا بعد N شمعة.
(function () {
  const DEF = { len: 5, minPct: 0, boxes: true, labels: true, structure: true, panel: true, confirm: true, dropped: true, colorUp: "#22c55e", colorDn: "#ef4444" };

  // closedN: عدد الشموع المغلقة — الشمعة الجارية لا تؤكد قمة (قد يتجاوزها السعر قبل إغلاقها)
  function pivots(b, len, closedN = b.length) {
    const out = [];
    for (let i = len; i < closedN - len; i++) {
      let hi = true, lo = true;
      for (let j = i - len; j <= i + len && (hi || lo); j++) {
        if (j === i) continue;
        // يسارًا: أعلى بشكل صارم، يمينًا: أعلى أو يساوي (حتى لا تُحسب القمة المسطحة مرتين)
        if (j < i ? b[j].high >= b[i].high : b[j].high > b[i].high) hi = false;
        if (j < i ? b[j].low <= b[i].low : b[j].low < b[i].low) lo = false;
      }
      // شمعة واحدة قمة وقاع معًا: الأقرب حسب لونها (الحمراء تصنع القمة أولًا ثم القاع)
      const both = hi && lo, red = b[i].close < b[i].open;
      if (hi && (!both || red)) out.push({ i, type: "H", p: b[i].high });
      if (lo) out.push({ i, type: "L", p: b[i].low });
      if (hi && both && !red) out.push({ i, type: "H", p: b[i].high });
    }
    return out;
  }

  // تتابع قمة ثم قاع ثم قمة...: قمتان متتاليتان تبقى الأعلى، وقاعان متتاليان يبقى الأدنى
  // dropped: نقاط تأكدت وظهرت ثم استُبدلت بنقطة أبعد منها (إعادة رسم) — تُحفظ ولا تُحذف
  function zigzag(piv, minPct, dropped = []) {
    const z = [];
    for (const v of piv) {
      const last = z[z.length - 1];
      if (!last) { z.push({ ...v }); continue; }
      if (last.type === v.type) {
        if (v.type === "H" ? v.p > last.p : v.p < last.p) { dropped.push({ ...last, by: v.i }); z[z.length - 1] = { ...v }; }
        continue;
      }
      // موجة أصغر من الحد الأدنى تُتجاهل (تبقى جزءًا من الموجة الأكبر)
      if (minPct > 0 && Math.abs(v.p / last.p - 1) * 100 < minPct) continue;
      z.push({ ...v });
    }
    return z;
  }

  function compute(b, cfg, closedN, stepSec) {
    if (b.length < cfg.len * 2 + 2) return null;
    const dropped = [], z = zigzag(pivots(b, cfg.len, closedN), cfg.minPct, dropped);
    // نقطة التأكيد: إغلاق الشمعة رقم len بعد القمة/القاع، وما فات من السعر حتى تلك اللحظة
    // القمة: (القمة − سعر التأكيد) ÷ القمة × 100 · القاع: (سعر التأكيد − القاع) ÷ القاع × 100
    const conf = v => {
      const ci = v.i + cfg.len, cp = b[ci].close;
      v.ci = ci; v.cp = cp; v.delay = (v.type === "H" ? v.p - cp : cp - v.p) / v.p * 100;
    };
    z.forEach(conf); dropped.forEach(conf);
    // تسمية البنية: كل قمة مقارنة بالقمة التي قبلها، وكل قاع بالقاع الذي قبله
    let lastH = null, lastL = null;
    for (const v of z) {
      if (v.type === "H") { v.tag = lastH == null ? null : v.p > lastH.p ? "HH" : v.p < lastH.p ? "LH" : "EH"; lastH = v; }
      else { v.tag = lastL == null ? null : v.p > lastL.p ? "HL" : v.p < lastL.p ? "LL" : "EL"; lastL = v; }
    }
    const waves = [];
    for (let k = 1; k < z.length; k++) {
      const a = z[k - 1], c = z[k], up = c.type === "H";
      waves.push({ n: k, from: a.i, to: c.i, up, lo: Math.min(a.p, c.p), hi: Math.max(a.p, c.p), pct: (c.p / a.p - 1) * 100, bars: c.i - a.i });
    }
    // الموجة الجارية: من آخر نقطة مؤكدة حتى أبعد سعر بعدها (لم تتأكد نهايتها بعد)
    let current = null;
    const lastP = z[z.length - 1];
    if (lastP && lastP.i < b.length - 1) {
      let ext = lastP.i + 1;
      for (let i = lastP.i + 2; i < b.length; i++)
        if (lastP.type === "H" ? b[i].low < b[ext].low : b[i].high > b[ext].high) ext = i;
      const up = lastP.type === "L", endP = up ? b[ext].high : b[ext].low;
      if ((up ? endP > lastP.p : endP < lastP.p))
        current = { from: lastP.i, to: ext, up, lo: Math.min(lastP.p, endP), hi: Math.max(lastP.p, endP), pct: (endP / lastP.p - 1) * 100, bars: ext - lastP.i, last: b.length - 1 };
    }
    // الاتجاه من آخر قمة وآخر قاع
    const tH = lastH?.tag, tL = lastL?.tag;
    const trend = tH === "HH" && tL === "HL" ? "up" : tH === "LH" && tL === "LL" ? "down" : (tH && tL) ? "range" : null;
    const avg = a => a.length ? a.reduce((s, x) => s + Math.abs(x.pct), 0) / a.length : null;
    const avgDelay = t => { const a = z.filter(v => v.type === t); return a.length ? a.reduce((s, v) => s + v.delay, 0) / a.length : null; };
    return { z, waves, current, trend, tH, tL, avgUp: avg(waves.filter(w => w.up)), avgDn: avg(waves.filter(w => !w.up)), len: cfg.len,
             dropped, lastH, lastL, delayH: avgDelay("H"), delayL: avgDelay("L"), stepSec };
  }

  const TAGS = { HH: "قمة أعلى HH", LH: "قمة أدنى LH", EH: "قمة مساوية", HL: "قاع أعلى HL", LL: "قاع أدنى LL", EL: "قاع مساوٍ" };
  const TREND = {
    up: ["⬆ اتجاه صاعد", "قمم أعلى وقيعان أعلى: المشترون يرفعون السعر في كل موجة"],
    down: ["⬇ اتجاه هابط", "قمم أدنى وقيعان أدنى: البائعون يخفضون السعر في كل موجة"],
    range: ["↔ اتجاه غير واضح", "القمم والقيعان لا تتفقان (مثلًا قمة أعلى مع قاع أدنى): تذبذب أو تحوّل محتمل"],
  };

  // مدة التأكيد: عدد الشموع × مدة الشمعة، من إغلاق شمعة القمة إلى إغلاق شمعة التأكيد
  const waitTxt = (len, stepSec) => stepSec && stepSec < 86400
    ? `${len} شموع (${len * stepSec >= 3600 ? (len * stepSec / 3600).toFixed(len * stepSec % 3600 ? 1 : 0) + " ساعة" : len * stepSec / 60 + " دقيقة"})`
    : `${len} شموع`;

  window.initWaves = function ({ chart, series, container, getBars, nowS, step }) {
    let cfg = { ...DEF }, visible = false, res = null, sig = "";
    try { cfg = { ...DEF, ...(JSON.parse(localStorage.getItem("wv_cfg")) || {}) }; } catch {}
    const canvas = document.createElement("canvas");
    canvas.style.cssText = "position:absolute;inset:0;z-index:2;pointer-events:none";
    container.appendChild(canvas);
    const ctx = canvas.getContext("2d");
    const panel = document.createElement("div");
    panel.className = "wv-panel"; panel.hidden = true;
    container.appendChild(panel);
    const alpha = (hex, a) => { const v = parseInt(hex.slice(1), 16); return `rgba(${v >> 16},${(v >> 8) & 255},${v & 255},${a})`; };
    const pct = v => (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
    const fp = p => p.toFixed(Math.abs(p) < 1 ? 4 : 2);

    function recalc() {
      const b = getBars(), last = b[b.length - 1], st = step ? step() : 0;
      // الشمعة الأخيرة جارية إذا لم ينتهِ وقتها بعد، فلا تُستخدم لتأكيد قمة أو قاع
      const closedN = last && st && nowS && last.time + st > nowS() ? b.length - 1 : b.length;
      const s = last ? `${b.length}|${closedN}|${b[0].time}|${last.time}|${last.high}|${last.low}|${last.close}|${st}|${JSON.stringify(cfg)}` : "";
      if (s === sig) return;
      sig = s; res = visible && b.length ? compute(b, cfg, closedN, st) : null;
      renderPanel();
    }

    function renderPanel() {
      panel.hidden = !visible || !cfg.panel;
      if (panel.hidden) return;
      if (!res || !res.waves.length) { panel.innerHTML = `<b>📘 الموجات</b><div class="muted">لا توجد موجات مؤكدة بعد على هذا الفاصل</div>`; return; }
      const w = res.waves[res.waves.length - 1], c = res.current, t = res.trend && TREND[res.trend];
      const col = up => up ? cfg.colorUp : cfg.colorDn;
      panel.innerHTML = `${t ? `<b style="color:${res.trend === "up" ? cfg.colorUp : res.trend === "down" ? cfg.colorDn : "#facc15"}">${t[0]}</b> <span class="muted">${t[1]}</span>` : "<b>📘 الموجات</b>"}
        <div>آخر قمة <b>${TAGS[res.tH] || "—"}</b> · آخر قاع <b>${TAGS[res.tL] || "—"}</b> · متوسط الصاعدة ${res.avgUp != null ? res.avgUp.toFixed(1) + "%" : "—"} والهابطة ${res.avgDn != null ? res.avgDn.toFixed(1) + "%" : "—"}</div>
        <div>آخر موجة (${w.n}) <b style="color:${col(w.up)}">${w.up ? "صاعدة" : "هابطة"} ${pct(w.pct)}</b> في ${w.bars} شمعة${c ? ` · الجارية <b style="color:${col(c.up)}">${c.up ? "صاعدة" : "هابطة"} ${pct(c.pct)}</b> (لم تكتمل)` : ""}</div>
        ${cfg.confirm ? [res.lastH, res.lastL].filter(Boolean).map(v => `<div>${v.type === "H" ? "آخر قمة" : "آخر قاع"} ${fp(v.p)} تأكد بعد ${waitTxt(res.len, res.stepSec)} عند ${fp(v.cp)}: فات <b>${v.delay.toFixed(2)}%</b></div>`).join("") +
          `<div class="muted">متوسط ما يفوت قبل التأكيد: القمم ${res.delayH != null ? res.delayH.toFixed(2) + "%" : "—"} · القيعان ${res.delayL != null ? res.delayL.toFixed(2) + "%" : "—"}${res.dropped.length ? ` · ${res.dropped.length} نقطة تأكدت ثم نُقلت (✕)` : ""}</div>` : ""}`;
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

    function drawWave(w, dashed, title) {
      const ts = chart.timeScale(), x1 = ts.logicalToCoordinate(w.from), x2 = ts.logicalToCoordinate(w.to);
      const yT = series.priceToCoordinate(w.hi), yB = series.priceToCoordinate(w.lo);
      if ([x1, x2, yT, yB].some(v => v == null)) return;
      const color = w.up ? cfg.colorUp : cfg.colorDn;
      ctx.setLineDash(dashed ? [5, 4] : []);
      if (cfg.boxes) {
        ctx.fillStyle = alpha(color, dashed ? 0.06 : 0.1); ctx.fillRect(x1, yT, x2 - x1, yB - yT);
        ctx.strokeStyle = alpha(color, 0.8); ctx.lineWidth = 1; ctx.strokeRect(x1, yT, x2 - x1, yB - yT);
      }
      // قطر الموجة من بدايتها إلى نهايتها
      ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.beginPath();
      ctx.moveTo(x1, w.up ? yB : yT); ctx.lineTo(x2, w.up ? yT : yB); ctx.stroke();
      ctx.setLineDash([]);
      if (!cfg.labels) return null;
      // عزل الأرقام باتجاه يسار-يمين (LRI…PDI) حتى لا تنقلب «‎-21.03%» إلى «%21.03-» داخل النص العربي
      const iso = t => "\u2066" + t + "\u2069";
      return { txt: `${title} ${w.up ? "↑" : "↓"} ${iso(pct(w.pct))} · ${iso(w.bars)} شمعة`, x: (x1 + x2) / 2, y: w.up ? yT - 6 : yB + 14, color, size: Math.abs(w.pct) };
    }

    // النصوص لا تتراكب: الجارية أولًا ثم الأكبر نسبة، وأي نص يتقاطع مع نص سبقه لا يُكتب (كبّر الشارت لتظهر)
    function placeTexts(items, font, dir, placed = []) {
      ctx.font = font; ctx.direction = dir; ctx.textAlign = "center";
      for (const t of items) {
        const w = ctx.measureText(t.txt).width + 6, box = { l: t.x - w / 2, r: t.x + w / 2, t: t.y - 11, b: t.y + 3 };
        if (placed.some(p => box.l < p.r && box.r > p.l && box.t < p.b && box.b > p.t)) continue;
        placed.push(box); ctx.fillStyle = t.color; ctx.fillText(t.txt, t.x, t.y);
      }
      return placed;
    }

    function render() {
      const r = resize();
      ctx.clearRect(0, 0, r.width, r.height);
      if (visible) {
        recalc();
        if (res) {
          const labels = res.waves.map(w => drawWave(w, false, `موجة ${w.n}`)).filter(Boolean).sort((a, b) => b.size - a.size);
          const cur = res.current ? drawWave(res.current, true, "جارية") : null;
          const placed = placeTexts(cur ? [cur, ...labels] : labels, "11px system-ui, sans-serif", "rtl");
          const ts0 = chart.timeScale(), late = [];
          if (cfg.confirm) for (const v of res.z) {
            // خط منقط من القمة/القاع إلى شمعة التأكيد، ونقطة مفرغة عند سعر الإغلاق وقتها
            const x1 = ts0.logicalToCoordinate(v.i), y1 = series.priceToCoordinate(v.p), x2 = ts0.logicalToCoordinate(v.ci), y2 = series.priceToCoordinate(v.cp);
            if ([x1, y1, x2, y2].some(t => t == null)) continue;
            const color = v.type === "H" ? cfg.colorDn : cfg.colorUp;
            ctx.setLineDash([2, 3]); ctx.strokeStyle = color; ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); ctx.setLineDash([]);
            ctx.beginPath(); ctx.arc(x2, y2, 3.5, 0, Math.PI * 2); ctx.fillStyle = "#0f1419"; ctx.fill(); ctx.stroke();
            late.push({ txt: `✓ ${v.type === "H" ? "-" : "+"}${v.delay.toFixed(2)}%`, x: x2 + 22, y: v.type === "H" ? y2 + 14 : y2 - 6, color, size: v.i });
          }
          if (cfg.dropped) for (const v of res.dropped) {
            const x = ts0.logicalToCoordinate(v.i), y = series.priceToCoordinate(v.p);
            if (x == null || y == null) continue;
            ctx.strokeStyle = "#94a3b8"; ctx.lineWidth = 1.2; ctx.beginPath();
            ctx.moveTo(x - 4, y - 4); ctx.lineTo(x + 4, y + 4); ctx.moveTo(x + 4, y - 4); ctx.lineTo(x - 4, y + 4); ctx.stroke();
          }
          if (cfg.structure) {
            const ts = chart.timeScale(), tags = [];
            for (const v of res.z) {
              if (!v.tag) continue;
              const x = ts.logicalToCoordinate(v.i), y = series.priceToCoordinate(v.p);
              if (x == null || y == null) continue;
              const good = v.tag === "HH" || v.tag === "HL";
              tags.push({ txt: v.tag[0] === "E" ? "=" : v.tag, x, y: v.type === "H" ? y - (cfg.labels ? 20 : 8) : y + (cfg.labels ? 28 : 16),
                          color: v.tag[0] === "E" ? "#facc15" : good ? cfg.colorUp : cfg.colorDn, size: v.i });
            }
            placeTexts(tags.sort((a, b) => b.size - a.size), "bold 10.5px system-ui, sans-serif", "ltr", placed);  // الأحدث أولًا
          }
          placeTexts(late.sort((a, b) => b.size - a.size), "10px system-ui, sans-serif", "ltr", placed);
        }
      }
      requestAnimationFrame(render);
    }
    requestAnimationFrame(render);

    return {
      DEF,
      get config() { return { ...cfg }; },
      get state() { return res; },
      setConfig(c) { cfg = { ...cfg, ...c }; sig = ""; try { localStorage.setItem("wv_cfg", JSON.stringify(cfg)); } catch {} recalc(); },
      setVisible(v) { if (v === visible) return; visible = v; sig = ""; recalc(); renderPanel(); },
      refresh() { sig = ""; recalc(); },
    };
  };
})();
