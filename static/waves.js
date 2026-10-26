// مؤشر «الموجات» للتعلّم: يقسم الحركة إلى موجات صاعدة وهابطة، كل موجة في مربع،
// ويسمّي القمم والقيعان (قمة أعلى/أدنى، قاع أعلى/أدنى) ويستنتج الاتجاه منها.
// القمة = أعلى سعر بين N شمعة قبلها و N شمعة بعدها (مثل ta.pivothigh)، فلا تتأكد إلا بعد N شمعة.
(function () {
  const DEF = { len: 5, minPct: 0, boxes: true, labels: true, structure: true, panel: true, colorUp: "#22c55e", colorDn: "#ef4444" };

  function pivots(b, len) {
    const out = [];
    for (let i = len; i < b.length - len; i++) {
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
  function zigzag(piv, minPct) {
    const z = [];
    for (const v of piv) {
      const last = z[z.length - 1];
      if (!last) { z.push({ ...v }); continue; }
      if (last.type === v.type) {
        if (v.type === "H" ? v.p > last.p : v.p < last.p) z[z.length - 1] = { ...v };
        continue;
      }
      // موجة أصغر من الحد الأدنى تُتجاهل (تبقى جزءًا من الموجة الأكبر)
      if (minPct > 0 && Math.abs(v.p / last.p - 1) * 100 < minPct) continue;
      z.push({ ...v });
    }
    return z;
  }

  function compute(b, cfg) {
    if (b.length < cfg.len * 2 + 2) return null;
    const z = zigzag(pivots(b, cfg.len), cfg.minPct);
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
    return { z, waves, current, trend, tH, tL, avgUp: avg(waves.filter(w => w.up)), avgDn: avg(waves.filter(w => !w.up)), len: cfg.len };
  }

  const TAGS = { HH: "قمة أعلى HH", LH: "قمة أدنى LH", EH: "قمة مساوية", HL: "قاع أعلى HL", LL: "قاع أدنى LL", EL: "قاع مساوٍ" };
  const TREND = {
    up: ["⬆ اتجاه صاعد", "قمم أعلى وقيعان أعلى: المشترون يرفعون السعر في كل موجة"],
    down: ["⬇ اتجاه هابط", "قمم أدنى وقيعان أدنى: البائعون يخفضون السعر في كل موجة"],
    range: ["↔ اتجاه غير واضح", "القمم والقيعان لا تتفقان (مثلًا قمة أعلى مع قاع أدنى): تذبذب أو تحوّل محتمل"],
  };

  window.initWaves = function ({ chart, series, container, getBars }) {
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

    function recalc() {
      const b = getBars(), last = b[b.length - 1];
      const s = last ? `${b.length}|${b[0].time}|${last.time}|${last.high}|${last.low}|${last.close}|${JSON.stringify(cfg)}` : "";
      if (s === sig) return;
      sig = s; res = visible && b.length ? compute(b, cfg) : null;
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
        <div>آخر موجة (${w.n}) <b style="color:${col(w.up)}">${w.up ? "صاعدة" : "هابطة"} ${pct(w.pct)}</b> في ${w.bars} شمعة${c ? ` · الجارية <b style="color:${col(c.up)}">${c.up ? "صاعدة" : "هابطة"} ${pct(c.pct)}</b> (لم تكتمل)` : ""}</div>`;
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
      if (cfg.labels && x2 - x1 > 18) {
        const txt = `${title} ${w.up ? "↑" : "↓"} ${pct(w.pct)} · ${w.bars} شمعة`;
        ctx.font = "11px system-ui, sans-serif"; ctx.direction = "rtl"; ctx.textAlign = "center";
        const y = w.up ? yT - 6 : yB + 14;
        ctx.fillStyle = color; ctx.fillText(txt, (x1 + x2) / 2, y);
      }
    }

    function render() {
      const r = resize();
      ctx.clearRect(0, 0, r.width, r.height);
      if (visible) {
        recalc();
        if (res) {
          res.waves.forEach(w => drawWave(w, false, `موجة ${w.n}`));
          if (res.current) drawWave(res.current, true, "جارية");
          if (cfg.structure) {
            ctx.font = "bold 10.5px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.direction = "ltr";
            const ts = chart.timeScale();
            for (const v of res.z) {
              if (!v.tag) continue;
              const x = ts.logicalToCoordinate(v.i), y = series.priceToCoordinate(v.p);
              if (x == null || y == null) continue;
              const good = v.tag === "HH" || v.tag === "HL";
              ctx.fillStyle = v.tag[0] === "E" ? "#facc15" : good ? cfg.colorUp : cfg.colorDn;
              ctx.fillText(v.tag[0] === "E" ? "=" : v.tag, x, v.type === "H" ? y - (cfg.labels ? 20 : 8) : y + (cfg.labels ? 28 : 16));
            }
          }
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
