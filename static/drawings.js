// أدوات الرسم على الشارت اللحظي (Lightweight Charts لا يحتوي أدوات رسم).
// كل رسمة تُحفظ بنقاط (وقت، سعر) فتتحرك مع التكبير والتمرير وتعمل على كل الفواصل،
// وتُحفظ في المتصفح لكل سهم.
(function () {
  const TOOLS = [
    { id: "cursor", icon: "↖", title: "مؤشر (بدون رسم)" },
    { id: "hline", icon: "―", title: "خط أفقي (دعم / مقاومة)", points: 1 },
    { id: "trend", icon: "╱", title: "خط اتجاه", points: 2 },
    { id: "ray", icon: "↗", title: "شعاع (يمتد لليمين)", points: 2 },
    { id: "vline", icon: "│", title: "خط عمودي", points: 1 },
    { id: "rect", icon: "▭", title: "مستطيل (منطقة)", points: 2 },
    { id: "fib", icon: "𝐅", title: "فيبوناتشي", points: 2 },
    { id: "measure", icon: "⇕", title: "قياس (النسبة بين نقطتين)", points: 2 },
    { id: "text", icon: "T", title: "نص", points: 1 },
    { id: "erase", icon: "⌫", title: "ممحاة: اضغط على رسمة لحذفها" },
  ];
  const FIB = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
  const COLORS = ["#3b82f6", "#22c55e", "#ef4444", "#facc15", "#e5e7eb", "#a855f7"];

  window.initDrawings = function ({ chart, series, container, toolbar, symbol, getBars }) {
    const key = "drawings_" + symbol;
    const load = () => { try { return JSON.parse(localStorage.getItem(key)) || []; } catch { return []; } };
    const save = () => { try { localStorage.setItem(key, JSON.stringify(drawings)); } catch {} };
    let drawings = load();
    let tool = "cursor", color = COLORS[0], magnet = true;
    let pending = null;   // نقطة أولى لأداة ذات نقطتين
    let hover = null;     // موضع المؤشر الحالي للمعاينة

    // ===== الطبقة الشفافة فوق الشارت =====
    const canvas = document.createElement("canvas");
    canvas.style.cssText = "position:absolute;inset:0;z-index:3;pointer-events:none";
    container.appendChild(canvas);
    const ctx = canvas.getContext("2d");

    // ===== تحويل (وقت، سعر) ↔ (x، y) =====
    function step(b) {
      if (b.length < 2) return 60;
      const d = [];
      for (let i = Math.max(1, b.length - 50); i < b.length; i++) d.push(b[i].time - b[i - 1].time);
      d.sort((a, c) => a - c);
      return d[Math.floor(d.length / 2)] || 60;
    }
    function timeToX(t) {
      const b = getBars();
      if (!b.length) return null;
      const s = step(b);
      let lo = 0, hi = b.length - 1, logical;
      if (t <= b[0].time) logical = (t - b[0].time) / s;
      else if (t >= b[hi].time) logical = hi + (t - b[hi].time) / s;
      else {
        while (hi - lo > 1) { const m = (lo + hi) >> 1; b[m].time <= t ? lo = m : hi = m; }
        logical = lo + (t - b[lo].time) / (b[hi].time - b[lo].time);
      }
      return chart.timeScale().logicalToCoordinate(logical);
    }
    function xToTime(x) {
      const b = getBars();
      const l = chart.timeScale().coordinateToLogical(x);
      if (!b.length || l == null) return null;
      const s = step(b), i = Math.min(Math.max(Math.floor(l), 0), b.length - 1);
      return Math.round(b[i].time + (l - i) * s);
    }
    const priceToY = p => series.priceToCoordinate(p);
    const yToPrice = y => series.coordinateToPrice(y);

    // المغناطيس: يلتصق بأعلى/أدنى/افتتاح/إغلاق أقرب شمعة إن كان قريبًا
    function pointAt(x, y) {
      let time = xToTime(x), price = yToPrice(y);
      if (time == null || price == null) return null;
      if (magnet) {
        const b = getBars();
        const l = Math.round(chart.timeScale().coordinateToLogical(x));
        const bar = b[l];
        if (bar) {
          let best = null;
          for (const v of [bar.open, bar.high, bar.low, bar.close]) {
            const d = Math.abs(priceToY(v) - y);
            if (d < 12 && (!best || d < best.d)) best = { v, d };
          }
          if (best) { price = best.v; time = bar.time; }
        }
      }
      return { time, price };
    }

    const fmt = p => p == null ? "" : p.toFixed(p < 1 ? 4 : 2);

    // ===== الرسم =====
    function resize() {
      const dpr = window.devicePixelRatio || 1, r = container.getBoundingClientRect();
      if (canvas.width !== Math.round(r.width * dpr) || canvas.height !== Math.round(r.height * dpr)) {
        canvas.width = Math.round(r.width * dpr); canvas.height = Math.round(r.height * dpr);
        canvas.style.width = r.width + "px"; canvas.style.height = r.height + "px";
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.direction = "ltr";  // الأرقام والنسب من اليسار لليمين رغم أن الصفحة عربية
      return r;
    }

    function label(text, x, y, bg, dir = "ltr") {
      ctx.font = "11px system-ui, sans-serif";
      ctx.direction = dir; ctx.textAlign = "left";
      const w = ctx.measureText(text).width + 8;
      ctx.fillStyle = bg; ctx.fillRect(x, y - 8, w, 16);
      ctx.fillStyle = "#0f1419"; ctx.fillText(text, x + 4, y + 4);
      ctx.direction = "ltr";
    }

    function drawOne(d, W, H) {
      const [a, b] = d.points;
      const x1 = timeToX(a.time), y1 = priceToY(a.price);
      const x2 = b ? timeToX(b.time) : null, y2 = b ? priceToY(b.price) : null;
      ctx.strokeStyle = d.color; ctx.fillStyle = d.color; ctx.lineWidth = 1.5; ctx.setLineDash([]);
      ctx.beginPath();
      switch (d.type) {
        case "hline":
          if (y1 == null) return;
          ctx.moveTo(0, y1); ctx.lineTo(W, y1); ctx.stroke();
          label(fmt(a.price), W - 70, y1, d.color);
          break;
        case "vline":
          if (x1 == null) return;
          ctx.moveTo(x1, 0); ctx.lineTo(x1, H); ctx.stroke();
          break;
        case "trend":
        case "ray":
          if ([x1, y1, x2, y2].includes(null)) return;
          ctx.moveTo(x1, y1);
          if (d.type === "ray" && x2 !== x1) {
            const k = (y2 - y1) / (x2 - x1), xe = x2 > x1 ? W : 0;
            ctx.lineTo(xe, y1 + k * (xe - x1));
          } else ctx.lineTo(x2, y2);
          ctx.stroke();
          break;
        case "rect":
          if ([x1, y1, x2, y2].includes(null)) return;
          ctx.globalAlpha = 0.15; ctx.fillRect(x1, y1, x2 - x1, y2 - y1); ctx.globalAlpha = 1;
          ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
          break;
        case "fib": {
          if ([x1, y1, x2, y2].includes(null)) return;
          const left = Math.min(x1, x2), right = Math.max(x1, x2, left + 40);
          for (const lv of FIB) {
            const p = b.price + (a.price - b.price) * lv, y = priceToY(p);
            if (y == null) continue;
            ctx.globalAlpha = lv === 0 || lv === 1 ? 1 : 0.7;
            ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
            ctx.globalAlpha = 1;
            ctx.font = "11px system-ui, sans-serif";
            ctx.fillText(`${lv} (${fmt(p)})`, right + 4, y + 4);
          }
          ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); ctx.setLineDash([]);
          break;
        }
        case "measure": {
          if ([x1, y1, x2, y2].includes(null)) return;
          const up = b.price >= a.price, c = up ? "#22c55e" : "#ef4444";
          ctx.fillStyle = c; ctx.globalAlpha = 0.15; ctx.fillRect(x1, y1, x2 - x1, y2 - y1); ctx.globalAlpha = 1;
          ctx.strokeStyle = c; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
          const pct = (b.price / a.price - 1) * 100, bars = Math.round(Math.abs(b.time - a.time) / step(getBars()));
          label(`${pct >= 0 ? "+" : ""}${pct.toFixed(2)}% · ${fmt(Math.abs(b.price - a.price))}`, Math.max(x1, x2) + 6, y2, c);
          label(`عدد الشموع: ${bars}`, Math.max(x1, x2) + 6, y2 + 18, c, "rtl");
          break;
        }
        case "text":
          if (x1 == null || y1 == null) return;
          ctx.font = "13px system-ui, sans-serif"; ctx.direction = "rtl"; ctx.textAlign = "left";
          ctx.fillText(d.text, x1, y1); ctx.direction = "ltr";
          break;
      }
    }

    function render() {
      const r = resize();
      ctx.clearRect(0, 0, r.width, r.height);
      for (const d of drawings) drawOne(d, r.width, r.height);
      // معاينة الرسمة الجارية
      if (pending && hover) drawOne({ type: tool, color, points: [pending, hover] }, r.width, r.height);
      else if (hover && (tool === "hline" || tool === "vline")) {
        ctx.globalAlpha = 0.5; drawOne({ type: tool, color, points: [hover] }, r.width, r.height); ctx.globalAlpha = 1;
      }
      requestAnimationFrame(render);
    }
    requestAnimationFrame(render);

    // ===== الممحاة: أقرب رسمة للنقطة =====
    function distToSeg(px, py, x1, y1, x2, y2) {
      const dx = x2 - x1, dy = y2 - y1, len = dx * dx + dy * dy;
      const t = len ? Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / len)) : 0;
      return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
    }
    function hit(x, y) {
      for (let i = drawings.length - 1; i >= 0; i--) {
        const d = drawings[i], [a, b] = d.points;
        const x1 = timeToX(a.time), y1 = priceToY(a.price), x2 = b && timeToX(b.time), y2 = b && priceToY(b.price);
        let dist = Infinity;
        if (d.type === "hline") dist = Math.abs(y - y1);
        else if (d.type === "vline") dist = Math.abs(x - x1);
        else if (d.type === "text") dist = Math.hypot(x - x1, y - y1) - 10;
        else if (b && (d.type === "rect" || d.type === "fib" || d.type === "measure"))
          dist = x >= Math.min(x1, x2) - 4 && x <= Math.max(x1, x2) + 4 && y >= Math.min(y1, y2) - 4 && y <= Math.max(y1, y2) + 4 ? 0 : Infinity;
        else if (b) dist = distToSeg(x, y, x1, y1, x2, y2);
        if (dist < 7) return i;
      }
      return -1;
    }

    // ===== التفاعل =====
    const pos = e => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    canvas.addEventListener("pointermove", e => { const [x, y] = pos(e); hover = pointAt(x, y); });
    canvas.addEventListener("pointerleave", () => { hover = null; });
    canvas.addEventListener("pointerdown", e => {
      e.preventDefault();
      const [x, y] = pos(e);
      if (tool === "erase") {
        const i = hit(x, y);
        if (i >= 0) { drawings.splice(i, 1); save(); }
        return;
      }
      const p = pointAt(x, y);
      if (!p) return;
      const def = TOOLS.find(t => t.id === tool);
      if (def.points === 1) {
        const d = { type: tool, color, points: [p] };
        if (tool === "text") {
          const text = prompt("النص:");
          if (!text) return;
          d.text = text;
        }
        drawings.push(d); save(); setTool("cursor");
      } else if (!pending) {
        pending = p;
      } else {
        drawings.push({ type: tool, color, points: [pending, p] });
        pending = null; save(); setTool("cursor");
      }
    });
    document.addEventListener("keydown", e => {
      if (e.key === "Escape") { pending = null; setTool("cursor"); }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && drawings.length) { drawings.pop(); save(); }
    });

    // ===== شريط الأدوات =====
    function setTool(t) {
      tool = t; pending = null;
      canvas.style.pointerEvents = t === "cursor" ? "none" : "auto";
      canvas.style.cursor = t === "erase" ? "not-allowed" : "crosshair";
      toolbar.querySelectorAll("[data-tool]").forEach(b => b.classList.toggle("active", b.dataset.tool === t));
    }
    toolbar.innerHTML =
      TOOLS.map(t => `<button data-tool="${t.id}" title="${t.title}">${t.icon}</button>`).join("") +
      `<span class="tb-sep"></span>` +
      `<button data-act="magnet" title="المغناطيس: الالتصاق بأعلى/أدنى/افتتاح/إغلاق الشمعة" class="active">🧲</button>` +
      `<button data-act="color" title="اللون"><span id="tb-color" style="display:inline-block;width:14px;height:14px;border-radius:50%;background:${color}"></span></button>` +
      `<button data-act="undo" title="تراجع (Ctrl+Z)">↶</button>` +
      `<button data-act="clear" title="مسح كل الرسومات">🗑</button>`;
    toolbar.querySelectorAll("[data-tool]").forEach(b => b.onclick = () => setTool(b.dataset.tool));
    toolbar.querySelector('[data-act="magnet"]').onclick = e => { magnet = !magnet; e.currentTarget.classList.toggle("active", magnet); };
    toolbar.querySelector('[data-act="color"]').onclick = () => {
      color = COLORS[(COLORS.indexOf(color) + 1) % COLORS.length];
      document.getElementById("tb-color").style.background = color;
    };
    toolbar.querySelector('[data-act="undo"]').onclick = () => { drawings.pop(); save(); };
    toolbar.querySelector('[data-act="clear"]').onclick = () => {
      if (drawings.length && confirm("مسح كل الرسومات على هذا السهم؟")) { drawings = []; save(); }
    };
    setTool("cursor");
  };
})();
