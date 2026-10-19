// أدوات الرسم على الشارت اللحظي (Lightweight Charts لا يحتوي أدوات رسم).
// كل رسمة تُحفظ بنقاط (وقت، سعر) فتتحرك مع التكبير والتمرير وتعمل على كل الفواصل،
// وتُحفظ في المتصفح لكل سهم. في وضع المؤشر: اضغط رسمة لتحديدها، اسحبها لتحريكها،
// واسحب مقابضها لتعديلها، ومن شريط الخصائص غيّر اللون والسماكة والنمط.
(function () {
  const TOOLS = [
    { id: "cursor", icon: "↖", title: "مؤشر: تحديد وتحريك الرسومات" },
    { id: "hline", icon: "―", title: "خط أفقي (دعم / مقاومة)", points: 1 },
    { id: "trend", icon: "╱", title: "خط اتجاه", points: 2 },
    { id: "ray", icon: "↗", title: "شعاع (يمتد لليمين)", points: 2 },
    { id: "arrow", icon: "➚", title: "سهم", points: 2 },
    { id: "channel", icon: "⫽", title: "قناة متوازية (3 نقاط)", points: 3 },
    { id: "vline", icon: "│", title: "خط عمودي", points: 1 },
    { id: "rect", icon: "▭", title: "مستطيل (منطقة)", points: 2 },
    { id: "fib", icon: "𝐅", title: "فيبوناتشي", points: 2 },
    { id: "measure", icon: "⇕", title: "قياس (النسبة بين نقطتين)", points: 2 },
    { id: "text", icon: "T", title: "نص", points: 1 },
    { id: "erase", icon: "⌫", title: "ممحاة: اضغط على رسمة لحذفها" },
  ];
  const FIB = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
  const COLORS = ["#3b82f6", "#22c55e", "#ef4444", "#facc15", "#f97316", "#a855f7", "#06b6d4", "#e5e7eb"];
  const WIDTHS = [1, 2, 3, 4];
  const DASHES = { solid: [], dashed: [8, 5], dotted: [2, 4] };
  const HANDLE = 5;

  window.initDrawings = function ({ chart, series, container, toolbar, symbol, getBars }) {
    const key = "drawings_" + symbol;
    let drawings;
    try { drawings = JSON.parse(localStorage.getItem(key)) || []; } catch { drawings = []; }
    const save = () => { try { localStorage.setItem(key, JSON.stringify(drawings)); } catch {} };

    // سجل التراجع: نسخة قبل كل تعديل
    const history = [];
    const commit = () => { history.push(JSON.stringify(drawings)); if (history.length > 100) history.shift(); };
    const undo = () => { if (history.length) { drawings = JSON.parse(history.pop()); selected = -1; save(); showProps(); } };

    let tool = "cursor", color = COLORS[0], magnet = true, hidden = false;
    let pending = [];     // نقاط الرسمة الجارية
    let hover = null;     // موضع المؤشر الحالي (وقت، سعر)
    let selected = -1;    // الرسمة المحددة
    let drag = null;      // سحب جارٍ: { idx, handle, start, orig, moved }

    // ===== الطبقة الشفافة فوق الشارت =====
    const canvas = document.createElement("canvas");
    canvas.style.cssText = "position:absolute;inset:0;z-index:3;pointer-events:none";
    container.appendChild(canvas);
    const ctx = canvas.getContext("2d");

    // ===== شريط خصائص الرسمة المحددة =====
    const props = document.createElement("div");
    props.className = "draw-props";
    props.hidden = true;
    container.appendChild(props);

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
    function pointAt(x, y, snap = magnet) {
      let time = xToTime(x), price = yToPrice(y);
      if (time == null || price == null) return null;
      if (snap) {
        const bar = getBars()[Math.round(chart.timeScale().coordinateToLogical(x))];
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

    const fmt = p => p == null ? "" : p.toFixed(Math.abs(p) < 1 ? 4 : 2);
    const xy = p => [timeToX(p.time), priceToY(p.price)];

    // نقطة القناة الموازية: السعر على الخط الأساسي عند وقت النقطة الثالثة + الإزاحة
    function channelOffset(d) {
      const [a, b, c] = d.points;
      if (!c || b.time === a.time) return 0;
      return c.price - (a.price + (b.price - a.price) * (c.time - a.time) / (b.time - a.time));
    }

    // ===== الرسم =====
    function resize() {
      const dpr = window.devicePixelRatio || 1, r = container.getBoundingClientRect();
      if (canvas.width !== Math.round(r.width * dpr) || canvas.height !== Math.round(r.height * dpr)) {
        canvas.width = Math.round(r.width * dpr); canvas.height = Math.round(r.height * dpr);
        canvas.style.width = r.width + "px"; canvas.style.height = r.height + "px";
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.direction = "ltr";
      return r;
    }

    function label(text, x, y, bg, dir = "ltr") {
      ctx.save();
      ctx.setLineDash([]);
      ctx.font = "11px system-ui, sans-serif"; ctx.direction = dir; ctx.textAlign = "left";
      const w = ctx.measureText(text).width + 8;
      ctx.fillStyle = bg; ctx.fillRect(x, y - 8, w, 16);
      ctx.fillStyle = "#0f1419"; ctx.fillText(text, x + 4, y + 4);
      ctx.restore();
    }

    function line(x1, y1, x2, y2) { ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); }

    // يرجع مواضع المقابض لكل رسمة
    function handlesOf(d, W, H) {
      const pts = d.points.map(xy);
      if (d.type === "hline") return [[W / 2, pts[0][1]]];
      if (d.type === "vline") return [[pts[0][0], H / 2]];
      if (d.type === "rect" && pts[1]) {
        const [[x1, y1], [x2, y2]] = pts;
        return [[x1, y1], [x2, y2], [x1, y2], [x2, y1]];  // الزاويتان الإضافيتان 2 و 3
      }
      return pts;
    }

    function drawOne(d, W, H, isSel) {
      const pts = d.points.map(xy);
      if (pts.some(([x, y]) => x == null || y == null)) return;
      const [[x1, y1], [x2, y2] = []] = pts;
      ctx.save();
      ctx.strokeStyle = d.color; ctx.fillStyle = d.color;
      ctx.lineWidth = d.width || 1.5; ctx.setLineDash(DASHES[d.dash || "solid"]);
      switch (d.type) {
        case "hline":
          line(0, y1, W, y1);
          label(fmt(d.points[0].price), W - 74, y1, d.color);
          break;
        case "vline":
          line(x1, 0, x1, H);
          break;
        case "trend":
          line(x1, y1, x2, y2);
          break;
        case "ray": {
          if (x2 === x1) { line(x1, y1, x2, y2); break; }
          const k = (y2 - y1) / (x2 - x1), xe = x2 > x1 ? W : 0;
          line(x1, y1, xe, y1 + k * (xe - x1));
          break;
        }
        case "arrow": {
          line(x1, y1, x2, y2);
          const ang = Math.atan2(y2 - y1, x2 - x1), s = 10 + (d.width || 1.5) * 2;
          ctx.setLineDash([]); ctx.beginPath();
          ctx.moveTo(x2, y2);
          ctx.lineTo(x2 - s * Math.cos(ang - 0.45), y2 - s * Math.sin(ang - 0.45));
          ctx.lineTo(x2 - s * Math.cos(ang + 0.45), y2 - s * Math.sin(ang + 0.45));
          ctx.closePath(); ctx.fill();
          break;
        }
        case "channel": {
          line(x1, y1, x2, y2);
          if (pts[2]) {
            const off = channelOffset(d);
            const ya = priceToY(d.points[0].price + off), yb = priceToY(d.points[1].price + off);
            if (ya != null && yb != null) {
              line(x1, ya, x2, yb);
              ctx.globalAlpha = 0.1; ctx.beginPath();
              ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.lineTo(x2, yb); ctx.lineTo(x1, ya); ctx.closePath(); ctx.fill();
              ctx.globalAlpha = 1;
              ctx.setLineDash([4, 4]); ctx.globalAlpha = 0.6;
              line(x1, (y1 + ya) / 2, x2, (y2 + yb) / 2);  // خط المنتصف
              ctx.globalAlpha = 1;
            }
          }
          break;
        }
        case "rect":
          ctx.globalAlpha = 0.15; ctx.fillRect(x1, y1, x2 - x1, y2 - y1); ctx.globalAlpha = 1;
          ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
          break;
        case "fib": {
          const [a, b] = d.points;
          const left = Math.min(x1, x2), right = Math.max(x1, x2, left + 40);
          let prevY = null;
          FIB.forEach((lv, i) => {
            const p = b.price + (a.price - b.price) * lv, y = priceToY(p);
            if (y == null) return;
            if (prevY != null) { ctx.globalAlpha = i % 2 ? 0.06 : 0.12; ctx.fillRect(left, prevY, right - left, y - prevY); }
            ctx.globalAlpha = lv === 0 || lv === 1 ? 1 : 0.8;
            line(left, y, right, y);
            ctx.globalAlpha = 1;
            ctx.font = "11px system-ui, sans-serif";
            ctx.fillText(`${lv} (${fmt(p)})`, right + 4, y + 4);
            prevY = y;
          });
          ctx.setLineDash([4, 4]); ctx.lineWidth = 1; line(x1, y1, x2, y2);
          break;
        }
        case "measure": {
          const [a, b] = d.points, up = b.price >= a.price, c = up ? "#22c55e" : "#ef4444";
          ctx.fillStyle = c; ctx.strokeStyle = c;
          ctx.globalAlpha = 0.15; ctx.fillRect(x1, y1, x2 - x1, y2 - y1); ctx.globalAlpha = 1;
          line(x1, y1, x2, y2);
          const pct = (b.price / a.price - 1) * 100, n = Math.round(Math.abs(b.time - a.time) / step(getBars()));
          label(`${pct >= 0 ? "+" : ""}${pct.toFixed(2)}% · ${fmt(Math.abs(b.price - a.price))}`, Math.max(x1, x2) + 6, y2, c);
          label(`عدد الشموع: ${n}`, Math.max(x1, x2) + 6, y2 + 18, c, "rtl");
          break;
        }
        case "text":
          ctx.font = `${12 + (d.width || 1.5) * 2}px system-ui, sans-serif`;
          ctx.direction = "rtl"; ctx.textAlign = "left";
          ctx.fillText(d.text, x1, y1);
          break;
      }
      if (isSel) {
        ctx.setLineDash([]); ctx.lineWidth = 1.5;
        for (const [hx, hy] of handlesOf(d, W, H)) {
          ctx.beginPath(); ctx.arc(hx, hy, HANDLE, 0, Math.PI * 2);
          ctx.fillStyle = "#0f1419"; ctx.fill(); ctx.strokeStyle = d.color; ctx.stroke();
        }
      }
      ctx.restore();
    }

    function render() {
      const r = resize();
      ctx.clearRect(0, 0, r.width, r.height);
      if (!hidden) drawings.forEach((d, i) => drawOne(d, r.width, r.height, i === selected));
      // معاينة الرسمة الجارية
      if (pending.length && hover) drawOne({ type: tool, color, points: [...pending, hover] }, r.width, r.height, false);
      else if (hover && (tool === "hline" || tool === "vline")) {
        ctx.globalAlpha = 0.5; drawOne({ type: tool, color, points: [hover] }, r.width, r.height, false); ctx.globalAlpha = 1;
      }
      requestAnimationFrame(render);
    }
    requestAnimationFrame(render);

    // ===== تحديد الرسمة تحت المؤشر =====
    function distToSeg(px, py, x1, y1, x2, y2) {
      const dx = x2 - x1, dy = y2 - y1, len = dx * dx + dy * dy;
      const t = len ? Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / len)) : 0;
      return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
    }
    function inBox(x, y, x1, y1, x2, y2, pad = 4) {
      return x >= Math.min(x1, x2) - pad && x <= Math.max(x1, x2) + pad && y >= Math.min(y1, y2) - pad && y <= Math.max(y1, y2) + pad;
    }
    function bodyDist(d, x, y, W) {
      const pts = d.points.map(xy);
      const [[x1, y1], [x2, y2] = []] = pts;
      switch (d.type) {
        case "hline": return Math.abs(y - y1);
        case "vline": return Math.abs(x - x1);
        case "text": {
          ctx.font = `${12 + (d.width || 1.5) * 2}px system-ui, sans-serif`;
          const w = ctx.measureText(d.text).width;
          return inBox(x, y, x1, y1 - 16, x1 + w, y1 + 4) ? 0 : Infinity;
        }
        case "rect": case "fib": case "measure": return inBox(x, y, x1, y1, x2, y2) ? 0 : Infinity;
        case "ray": {
          if (x2 === x1) return distToSeg(x, y, x1, y1, x2, y2);
          const k = (y2 - y1) / (x2 - x1), xe = x2 > x1 ? W : 0;
          return distToSeg(x, y, x1, y1, xe, y1 + k * (xe - x1));
        }
        case "channel": {
          let dist = distToSeg(x, y, x1, y1, x2, y2);
          if (pts[2]) {
            const off = channelOffset(d), ya = priceToY(d.points[0].price + off), yb = priceToY(d.points[1].price + off);
            dist = Math.min(dist, distToSeg(x, y, x1, ya, x2, yb));
          }
          return dist;
        }
        default: return distToSeg(x, y, x1, y1, x2, y2);
      }
    }
    function hitTest(x, y) {
      const W = container.clientWidth, H = container.clientHeight;
      // المقابض أولًا للرسمة المحددة
      if (selected >= 0 && drawings[selected]) {
        const hs = handlesOf(drawings[selected], W, H);
        for (let h = 0; h < hs.length; h++) if (Math.hypot(x - hs[h][0], y - hs[h][1]) <= HANDLE + 4) return { idx: selected, handle: h };
      }
      for (let i = drawings.length - 1; i >= 0; i--) if (bodyDist(drawings[i], x, y, W) < 7) return { idx: i, handle: -1 };
      return null;
    }

    // ===== السحب: تحريك الرسمة كاملة أو نقطة منها =====
    function applyDrag(x, y) {
      const d = drawings[drag.idx], o = drag.orig;
      const now = pointAt(x, y, drag.handle >= 0 && magnet), start = drag.start;
      if (!now) return;
      if (drag.handle < 0) {
        // تحريك كامل: إزاحة الوقت والسعر لكل النقاط
        const dt = (xToTime(x) ?? start.time) - start.time, dp = (yToPrice(y) ?? start.price) - start.price;
        d.points = o.map(p => ({
          time: d.type === "hline" ? p.time : Math.round(p.time + dt),
          price: d.type === "vline" ? p.price : p.price + dp,
        }));
      } else if (d.type === "rect" && drag.handle >= 2) {
        // الزوايا الإضافية في المستطيل: تخلط وقت نقطة مع سعر الأخرى
        const [a, b] = o;
        d.points = drag.handle === 2
          ? [{ time: now.time, price: a.price }, { time: b.time, price: now.price }]
          : [{ time: a.time, price: now.price }, { time: now.time, price: b.price }];
      } else {
        d.points = o.map((p, i) => i === drag.handle
          ? { time: d.type === "hline" ? p.time : now.time, price: d.type === "vline" ? p.price : now.price }
          : p);
      }
    }

    // ===== الأحداث (طبقة الالتقاط فوق أحداث الشارت) =====
    const pos = e => { const r = container.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    let consume = false;  // يمنع الشارت من التمرير عندما نتعامل نحن مع الضغطة
    const inProps = e => props.contains(e.target);

    container.addEventListener("pointerdown", e => {
      if (inProps(e) || e.button > 0) return;
      const [x, y] = pos(e);
      consume = false;

      if (tool === "erase") {
        consume = true;
        const h = hitTest(x, y);
        if (h) { commit(); drawings.splice(h.idx, 1); selected = -1; save(); showProps(); }
      } else if (tool !== "cursor") {
        consume = true;
        const p = pointAt(x, y);
        if (!p) return;
        pending.push(p);
        const def = TOOLS.find(t => t.id === tool);
        if (pending.length >= def.points) {
          const d = { type: tool, color, width: 1.5, dash: "solid", points: pending };
          pending = [];
          if (tool === "text") {
            const text = prompt("النص:");
            if (!text) { setTool("cursor"); return; }
            d.text = text; d.width = 2;
          }
          commit(); drawings.push(d); selected = drawings.length - 1; save();
          setTool("cursor"); showProps();
        }
      } else if (!hidden) {
        const h = hitTest(x, y);
        if (h) {
          consume = true;
          selected = h.idx;
          const d = drawings[h.idx];
          if (!d.locked) {
            drag = { idx: h.idx, handle: h.handle, start: { time: xToTime(x), price: yToPrice(y) }, orig: d.points.map(p => ({ ...p })), moved: false };
            container.setPointerCapture(e.pointerId);
          }
          showProps();
        } else if (selected >= 0) {
          selected = -1; showProps();
        }
      }
      if (consume) { e.stopPropagation(); e.preventDefault(); }
    }, true);

    // Lightweight Charts يستمع لأحداث الماوس/اللمس؛ نوقفها عندما نتعامل مع الضغطة
    for (const type of ["mousedown", "touchstart"]) {
      container.addEventListener(type, e => { if (consume && !inProps(e)) { e.stopPropagation(); } }, true);
    }

    container.addEventListener("pointermove", e => {
      if (inProps(e)) return;
      const [x, y] = pos(e);
      hover = tool !== "cursor" && tool !== "erase" ? pointAt(x, y) : null;
      if (drag) {
        if (!drag.moved) { commit(); drag.moved = true; }
        applyDrag(x, y);
        e.stopPropagation();
        return;
      }
      if (tool === "cursor" && !hidden) {
        const h = hitTest(x, y);
        container.style.cursor = h ? (h.handle >= 0 ? "grab" : drawings[h.idx].locked ? "not-allowed" : "move") : "";
      } else {
        container.style.cursor = tool === "erase" ? "not-allowed" : "crosshair";
      }
    }, true);

    container.addEventListener("pointerup", e => {
      if (drag) {
        if (drag.moved) save();
        try { container.releasePointerCapture(e.pointerId); } catch {}
        drag = null;
      }
      consume = false;
    }, true);
    container.addEventListener("pointerleave", () => { hover = null; });

    container.addEventListener("dblclick", e => {
      if (inProps(e)) return;
      const [x, y] = pos(e), h = hitTest(x, y);
      if (h && drawings[h.idx].type === "text") { editText(h.idx); e.stopPropagation(); }
    }, true);

    document.addEventListener("keydown", e => {
      if (["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
      if (e.key === "Escape") { pending = []; selected = -1; setTool("cursor"); showProps(); }
      if ((e.key === "Delete" || e.key === "Backspace") && selected >= 0) {
        commit(); drawings.splice(selected, 1); selected = -1; save(); showProps(); e.preventDefault();
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { undo(); e.preventDefault(); }
    });

    function editText(i) {
      const text = prompt("النص:", drawings[i].text);
      if (text) { commit(); drawings[i].text = text; save(); }
    }

    // ===== شريط خصائص الرسمة المحددة =====
    function showProps() {
      const d = drawings[selected];
      props.hidden = !d;
      if (!d) return;
      const btn = (attrs, inner, title, active) => `<button ${attrs} title="${title}" class="${active ? "active" : ""}">${inner}</button>`;
      props.innerHTML =
        COLORS.map(c => btn(`data-color="${c}"`, `<span class="dot" style="background:${c}"></span>`, "اللون", d.color === c)).join("") +
        `<span class="sep"></span>` +
        WIDTHS.map(w => btn(`data-width="${w}"`, `<span class="ln" style="height:${w}px"></span>`, `السماكة ${w}`, (d.width || 1.5) === w || (w === 1 && d.width === 1.5))).join("") +
        `<span class="sep"></span>` +
        btn('data-dash="solid"', "―", "خط متصل", (d.dash || "solid") === "solid") +
        btn('data-dash="dashed"', "╌", "خط متقطع", d.dash === "dashed") +
        btn('data-dash="dotted"', "┈", "خط منقط", d.dash === "dotted") +
        `<span class="sep"></span>` +
        (d.type === "text" ? btn('data-act="edit"', "✎", "تعديل النص") : "") +
        btn('data-act="lock"', d.locked ? "🔒" : "🔓", d.locked ? "فك القفل" : "قفل (منع التحريك)", d.locked) +
        btn('data-act="clone"', "⧉", "نسخ") +
        btn('data-act="delete"', "🗑", "حذف (Delete)");
    }
    props.addEventListener("pointerdown", e => e.stopPropagation());
    props.addEventListener("click", e => {
      const b = e.target.closest("button"), d = drawings[selected];
      if (!b || !d) return;
      commit();
      if (b.dataset.color) { d.color = b.dataset.color; color = d.color; syncColor(); }
      if (b.dataset.width) d.width = Number(b.dataset.width);
      if (b.dataset.dash) d.dash = b.dataset.dash;
      const act = b.dataset.act;
      if (act === "lock") d.locked = !d.locked;
      if (act === "edit") { history.pop(); editText(selected); }
      if (act === "clone") {
        const c = JSON.parse(JSON.stringify(d)), s = step(getBars());
        c.points = c.points.map(p => ({ time: p.time + s * 5, price: p.price }));
        c.locked = false;
        drawings.push(c); selected = drawings.length - 1;
      }
      if (act === "delete") { drawings.splice(selected, 1); selected = -1; }
      save(); showProps();
    });

    // ===== شريط الأدوات =====
    function setTool(t) {
      tool = t; pending = [];
      if (t !== "cursor") { selected = -1; showProps(); }
      container.style.cursor = t === "cursor" ? "" : t === "erase" ? "not-allowed" : "crosshair";
      toolbar.querySelectorAll("[data-tool]").forEach(b => b.classList.toggle("active", b.dataset.tool === t));
    }
    function syncColor() { const el = document.getElementById("tb-color"); if (el) el.style.background = color; }
    toolbar.innerHTML =
      TOOLS.map(t => `<button data-tool="${t.id}" title="${t.title}">${t.icon}</button>`).join("") +
      `<span class="tb-sep"></span>` +
      `<button data-act="magnet" title="المغناطيس: الالتصاق بأعلى/أدنى/افتتاح/إغلاق الشمعة" class="active">🧲</button>` +
      `<button data-act="color" title="لون الرسمة القادمة"><span id="tb-color" style="display:inline-block;width:14px;height:14px;border-radius:50%;background:${color}"></span></button>` +
      `<button data-act="hide" title="إخفاء / إظهار كل الرسومات">👁</button>` +
      `<button data-act="undo" title="تراجع (Ctrl+Z)">↶</button>` +
      `<button data-act="clear" title="مسح كل الرسومات">🗑</button>`;
    toolbar.querySelectorAll("[data-tool]").forEach(b => b.onclick = () => setTool(b.dataset.tool));
    toolbar.querySelector('[data-act="magnet"]').onclick = e => { magnet = !magnet; e.currentTarget.classList.toggle("active", magnet); };
    toolbar.querySelector('[data-act="color"]').onclick = () => { color = COLORS[(COLORS.indexOf(color) + 1) % COLORS.length]; syncColor(); };
    toolbar.querySelector('[data-act="hide"]').onclick = e => {
      hidden = !hidden; selected = -1; showProps();
      e.currentTarget.classList.toggle("active", hidden);
      e.currentTarget.textContent = hidden ? "🙈" : "👁";
    };
    toolbar.querySelector('[data-act="undo"]').onclick = undo;
    toolbar.querySelector('[data-act="clear"]').onclick = () => {
      if (drawings.length && confirm("مسح كل الرسومات على هذا السهم؟")) { commit(); drawings = []; selected = -1; save(); showProps(); }
    };
    setTool("cursor");
  };
})();
