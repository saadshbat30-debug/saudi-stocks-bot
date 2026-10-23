// 💼 صفقتي: خانة عائمة ثابتة في كل الصفحات — سعر الشراء والكمية والمبلغ والوقف والأهداف والربح/الخسارة لحظيًا.
// الصفقات تُحفظ في المتصفح (positions)، وصفحة السهم ترسم خطوطها على الشارت عند حدث "positions-changed".
(function () {
  const KEY = "positions", ACTIVE = "pos_active", UI = "pos_widget";
  const LEVELS = {
    stop1: { label: "وقف 1", color: "#ef4444", style: 0, icon: "🛑" },
    stop2: { label: "وقف 2", color: "#b91c1c", style: 2, icon: "🛑" },
    target1: { label: "هدف 1", color: "#22c55e", style: 0, icon: "🎯" },
    target2: { label: "هدف 2", color: "#15803d", style: 2, icon: "🎯" },
  };
  const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
  const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} };
  const all = () => load(KEY, {});
  const num = v => { const n = parseFloat(String(v ?? "").replace(/[,$\s]/g, "")); return isFinite(n) && n > 0 ? n : null; };
  const money = v => (v < 0 ? "-" : "") + "$" + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const px = v => v == null ? "—" : "$" + v.toFixed(v < 1 ? 4 : 2);
  const sgn = v => (v >= 0 ? "+" : "");
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const PAGE = window.PAGE_SYMBOL || null;

  // السهم المعروض: سهم الصفحة إن كانت له صفقة، وإلا آخر صفقة فُتحت، وإلا سهم الصفحة
  let sym = (PAGE && all()[PAGE]) ? PAGE : load(ACTIVE, null) && all()[load(ACTIVE, null)] ? load(ACTIVE, null) : PAGE;
  let prices = {}, pushed = {}, purify = {}, ui = load(UI, { min: false });
  if ("x" in ui || "y" in ui) { delete ui.x; delete ui.y; save(UI, ui); }

  const css = document.createElement("style");
  css.textContent = `
  .pw { position:fixed; z-index:50; width:300px; max-height:calc(100vh - 20px); display:flex; flex-direction:column; background:linear-gradient(180deg,#17212c,#11171d);
        border:1px solid #3b82f6; border-radius:12px; box-shadow:0 12px 32px rgba(0,0,0,.55); font-size:12.5px; color:var(--text); }
  .pw-h { display:flex; align-items:center; gap:6px; padding:6px 8px; border-bottom:1px solid var(--line); cursor:grab; user-select:none; }
  .pw-h b { white-space:nowrap; }
  .pw-h .pw-sum { margin-inline-start:auto; direction:ltr; font-weight:700; white-space:nowrap; }
  .pw-h button { background:var(--bg); border:1px solid var(--line); color:var(--muted); border-radius:6px; font:inherit; font-size:11px; padding:1px 7px; cursor:pointer; }
  .pw-h button:hover { color:var(--text); border-color:#3b82f6; }
  .pw.min .pw-b { display:none; } .pw.min { width:auto; min-width:230px; }
  .pw-b { overflow:auto; padding:8px 10px; display:flex; flex-direction:column; gap:7px; }
  .pw-tabs { display:flex; flex-wrap:wrap; gap:4px; align-items:center; }
  .pw-tabs button { padding:2px 8px; border-radius:999px; border:1px solid var(--line); background:var(--bg); color:var(--muted); cursor:pointer; font:inherit; font-size:11.5px; direction:ltr; }
  .pw-tabs button.on { background:#3b82f6; border-color:#3b82f6; color:#fff; }
  .pw-tabs input { width:76px; padding:2px 6px; border-radius:999px; border:1px dashed var(--line); background:var(--bg); color:var(--text); font:inherit; font-size:11.5px; direction:ltr; text-align:center; text-transform:uppercase; }
  .pw-row { display:flex; justify-content:space-between; align-items:center; gap:6px; }
  .pw-row a { color:#93c5fd; font-size:11.5px; } .pw-row a:hover { text-decoration:underline; }
  .pw-row .pw-btns { display:flex; gap:4px; }
  .pw-row .pw-btns button { background:var(--bg); border:1px solid var(--line); color:var(--muted); border-radius:6px; font:inherit; font-size:11px; padding:2px 7px; cursor:pointer; }
  .pw-row .pw-btns button:hover { color:var(--text); border-color:#3b82f6; }
  .pw-grid, .pw-lv { display:grid; grid-template-columns:repeat(3,1fr); gap:6px; } .pw-lv { grid-template-columns:1fr 1fr; }
  .pw label { display:flex; flex-direction:column; gap:3px; font-size:11px; color:var(--muted); min-width:0; }
  .pw input.f { width:100%; min-width:0; padding:6px; border-radius:7px; border:1px solid var(--line); background:#0f1419; color:var(--text); font:inherit; font-size:14px; font-weight:600; direction:ltr; text-align:center; font-variant-numeric:tabular-nums; }
  .pw input.f:focus { outline:none; border-color:#3b82f6; box-shadow:0 0 0 2px #3b82f633; }
  .pw .stop input.f { border-color:#7f1d1d; } .pw .tgt input.f { border-color:#14532d; }
  .pw .stop span { color:#fca5a5; font-weight:600; } .pw .tgt span { color:#86efac; font-weight:600; }
  .pw .lv em { font-style:normal; font-size:10.5px; direction:ltr; text-align:center; min-height:13px; color:var(--muted); }
  .pw .lv em.up { color:var(--up); } .pw .lv em.down { color:var(--down); }
  .pw .lv.hit input.f { background:#3d3315; border-color:#facc15; }
  .pw-pnl { display:flex; flex-direction:column; align-items:center; gap:2px; background:#0f1419; border:1px solid var(--line); border-radius:9px; padding:7px 8px; }
  .pw-pnl > span { font-size:11px; color:var(--muted); }
  .pw-pnl > b { font-size:19px; direction:ltr; font-variant-numeric:tabular-nums; text-align:center; }
  .pw-pnl.up { border-color:#14532d; background:#0f1f17; } .pw-pnl.down { border-color:#7f1d1d; background:#1f1212; }
  .pw-pnl.up > b, .pw-sum.up, .pw-all b.up { color:var(--up); } .pw-pnl.down > b, .pw-sum.down, .pw-all b.down { color:var(--down); }
  .pw-pnl small[hidden] { display:none; }
  .pw-pnl small { display:flex; gap:12px; font-size:11px; color:var(--muted); } .pw-pnl small b { color:var(--text); direction:ltr; }
  .pw-track { font-size:10.5px; color:var(--muted); }
  .pw-bar { position:relative; height:8px; border-radius:4px; background:linear-gradient(90deg,#ef4444,#3b4450 50%,#22c55e); direction:ltr; margin:4px 0 3px; }
  .pw-bar span { position:absolute; top:-3px; width:3px; height:14px; border-radius:2px; transform:translateX(-50%); }
  .pw-bar .b { background:#60a5fa; } .pw-bar .n { background:#fff; width:5px; box-shadow:0 0 0 2px #0f1419; transition:left .4s; }
  .pw-ends { display:flex; justify-content:space-between; direction:ltr; } .pw-rr { text-align:center; margin-top:2px; color:var(--text); }
  .pw-all { font-size:11.5px; } .pw-all div { display:flex; justify-content:space-between; gap:6px; padding:3px 0; border-bottom:1px solid #222a33; cursor:pointer; color:var(--muted); }
  .pw-all div:hover { color:var(--text); } .pw-all div.t { border:0; color:var(--text); font-weight:600; cursor:default; } .pw-all b { direction:ltr; }
  .pw-empty { color:var(--muted); text-align:center; padding:6px 0; }
  @media (max-width:700px) { .pw { width:calc(100vw - 16px); } }`;
  document.head.appendChild(css);

  const box = document.createElement("div");
  box.className = "pw" + (ui.min ? " min" : "");
  box.innerHTML = `
    <div class="pw-h" title="اسحب لتحريك الخانة · نقرتان لإرجاعها للزاوية">
      <span>⠿</span><b>💼 صفقتي</b><span class="pw-sum" id="pw-sum"></span>
      <button type="button" data-a="min" title="تصغير / تكبير">${ui.min ? "▢" : "_"}</button>
    </div>
    <div class="pw-b">
      <div class="pw-tabs" id="pw-tabs"></div>
      <div id="pw-main"></div>
      <div class="pw-all" id="pw-all"></div>
    </div>`;
  document.body.appendChild(box);
  const $ = id => box.querySelector("#" + id);

  // ===== المكان: يُسحب من العنوان ويُحفظ (افتراضيًا أسفل يسار الشاشة) =====
  // المكان مثبت على زاوية الشاشة اليمنى السفلية (بعدها عن الحافتين)، فيبقى في نفس المكان بكل الصفحات
  // مهما اختلف ارتفاع الخانة أو حجم النافذة. يمكن سحبه ويُحفظ مكانه الجديد بنفس الطريقة.
  function place() {
    const w = box.offsetWidth, h = box.offsetHeight;
    const r = Math.max(4, Math.min(window.innerWidth - w - 4, ui.r ?? 12));
    const b = Math.max(4, Math.min(window.innerHeight - Math.min(h, 60) - 4, ui.b ?? 12));
    box.style.left = box.style.top = "auto";
    box.style.right = r + "px";
    box.style.bottom = b + "px";
  }
  box.querySelector(".pw-h").addEventListener("pointerdown", e => {
    if (e.target.closest("button")) return;
    const rect = box.getBoundingClientRect();
    const dx = rect.right - e.clientX, dy = rect.bottom - e.clientY;
    const move = ev => { ui.r = window.innerWidth - ev.clientX - dx; ui.b = window.innerHeight - ev.clientY - dy; place(); };
    const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); save(UI, ui); };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  });
  // نقرتان على العنوان = إرجاعها للزاوية
  box.querySelector(".pw-h").addEventListener("dblclick", e => {
    if (e.target.closest("button")) return;
    ui.r = ui.b = 12; save(UI, ui); place();
  });
  addEventListener("resize", place);
  box.querySelector('[data-a="min"]').onclick = e => {
    ui.min = !ui.min; save(UI, ui); box.classList.toggle("min", ui.min); e.target.textContent = ui.min ? "▢" : "_";
    requestAnimationFrame(place);
  };

  // ===== السعر حسب الجلسة الحالية (رسمية / قبل وبعد / ليلية) =====
  function sessionPrice(q) {
    if (!q) return null;
    const s = typeof marketSession === "function" ? marketSession()[1] : "open";
    if (s === "night" && q.night_price != null) return q.night_price;
    if (s !== "open" && q.ext_price != null) return q.ext_price;
    return q.price ?? null;
  }
  async function refresh() {
    const o = all(), syms = [...new Set([...Object.keys(o), sym].filter(Boolean))];
    if (!syms.length) return render();
    try {
      const q = await (await fetch("/api/quotes?symbols=" + syms.map(encodeURIComponent).join(","))).json();
      for (const s of syms) {
        if (q[s]) purify[s] = q[s].purify ?? null;
        if (Date.now() - (pushed[s] || 0) < 12000) continue;  // صفحة السهم أرسلت سعرها للتو: هو المرجع
        const p = sessionPrice(q[s]); if (p != null) prices[s] = p;
      }
    } catch {}
    render(); check();
  }

  const get = () => (sym && all()[sym]) || {};
  function put(p) {
    const o = all(), has = p.buy || p.qty || Object.keys(LEVELS).some(k => p[k]);
    if (has) o[sym] = { ...p, at: o[sym]?.at || Date.now() }; else delete o[sym];
    save(KEY, o);
    window.dispatchEvent(new CustomEvent("positions-changed"));
  }
  function choose(s) {
    sym = s ? s.toUpperCase() : null;
    if (sym) save(ACTIVE, sym);
    build(); refresh();
  }

  // ===== البناء =====
  function build() {
    const o = all(), list = Object.keys(o);
    const tabs = [...list];
    if (PAGE && !tabs.includes(PAGE)) tabs.push(PAGE);
    $("pw-tabs").innerHTML = tabs.map(s => `<button type="button" data-sym="${esc(s)}" class="${s === sym ? "on" : ""}">${esc(s)}${!o[s] ? " +" : ""}</button>`).join("") +
      `<input id="pw-new" placeholder="+ رمز" maxlength="12" title="اكتب رمز سهم ثم Enter لإضافة صفقة">`;
    if (!sym) { $("pw-main").innerHTML = `<div class="pw-empty">اكتب رمز السهم في الخانة أعلاه ثم Enter لتسجيل صفقة</div>`; return; }
    const p = get();
    $("pw-main").innerHTML = `
      <div class="pw-row"><a href="/stock/${encodeURIComponent(sym)}" title="فتح صفحة السهم">📈 ${esc(sym)}</a>
        <span class="pw-btns"><button type="button" id="pw-fill" title="ضع السعر الحالي كسعر شراء">📍 الآن</button><button type="button" id="pw-del" title="حذف الصفقة">✕</button></span></div>
      <div class="pw-grid">
        <label>سعر الشراء<input class="f" id="pw-buy" inputmode="decimal" placeholder="0.00" autocomplete="off" value="${p.buy ?? ""}"></label>
        <label>الكمية<input class="f" id="pw-qty" inputmode="decimal" placeholder="0" autocomplete="off" value="${p.qty ?? ""}"></label>
        <label>المبلغ $<input class="f" id="pw-cost" inputmode="decimal" placeholder="0.00" autocomplete="off" value="${p.buy && p.qty ? (p.buy * p.qty).toFixed(2) : ""}"></label>
      </div>
      <div class="pw-pnl" id="pw-pnl-box"><span>الربح / الخسارة</span><b id="pw-pnl">—</b>
        <small><span>السعر الآن <b id="pw-now">—</b></span><span>القيمة الآن <b id="pw-val">—</b></span></small>
        <small id="pw-pur" hidden title="مبلغ التطهير التقريبي = الربح × نسبة التطهير (من القائمة الشرعية المستوردة)">🧼 التطهير <b id="pw-pur-v">—</b></small></div>
      <div class="pw-lv">${["stop1", "target1", "stop2", "target2"].map(k => `
        <label class="lv ${k.startsWith("stop") ? "stop" : "tgt"}"><span>${LEVELS[k].icon} ${LEVELS[k].label}</span>
          <input class="f" data-lv="${k}" inputmode="decimal" placeholder="—" autocomplete="off" value="${p[k] ?? ""}"><em data-out="${k}"></em></label>`).join("")}
      </div>
      <div class="pw-track" id="pw-track" hidden>
        <div class="pw-bar"><span class="b" id="pw-tb"></span><span class="n" id="pw-tn"></span></div>
        <div class="pw-ends"><span id="pw-lo"></span><span id="pw-hi"></span></div><div class="pw-rr" id="pw-rr"></div>
      </div>`;
    $("pw-buy").oninput = () => edited("buy");
    $("pw-qty").oninput = () => edited("qty");
    $("pw-cost").oninput = () => edited("cost");
    box.querySelectorAll("[data-lv]").forEach(i => i.oninput = () => edited(i.dataset.lv));
    $("pw-fill").onclick = () => { if (prices[sym] != null) { $("pw-buy").value = prices[sym]; edited("buy"); } };
    $("pw-del").onclick = () => {
      if (!confirm(`حذف صفقة ${sym} والوقف والأهداف؟`)) return;
      put({}); const rest = Object.keys(all()); choose(rest[0] || PAGE);
    };
    render();
  }
  $("pw-tabs").addEventListener("click", e => { const b = e.target.closest("[data-sym]"); if (b) choose(b.dataset.sym); });
  $("pw-tabs").addEventListener("keydown", e => {
    if (e.target.id !== "pw-new" || e.key !== "Enter") return;
    const v = e.target.value.trim().toUpperCase();
    if (/^[A-Z0-9.\-^=]{1,12}$/.test(v)) choose(v);
  });
  $("pw-all").addEventListener("click", e => { const r = e.target.closest("[data-sym]"); if (r) choose(r.dataset.sym); });

  // أي خانتين تكفيان: الكمية × السعر = المبلغ، أو المبلغ ÷ السعر = الكمية
  function edited(field) {
    const old = get(), p = { ...old, buy: num($("pw-buy").value), qty: num($("pw-qty").value) };
    for (const k in LEVELS) p[k] = num(box.querySelector(`[data-lv="${k}"]`).value);
    const cost = num($("pw-cost").value);
    if (field === "cost" && p.buy && cost) { p.qty = +(cost / p.buy).toFixed(4); $("pw-qty").value = p.qty; }
    else if (p.buy && p.qty) $("pw-cost").value = (p.buy * p.qty).toFixed(2);
    else if (field !== "cost") $("pw-cost").value = "";
    p.hit = { ...(old.hit || {}) };
    for (const k in LEVELS) if (p[k] !== old[k]) delete p.hit[k];  // تغيير المستوى يعيد تفعيل تنبيهه
    const isNew = !all()[sym];
    put(p);
    if (isNew) { const keep = document.activeElement?.id || document.activeElement?.dataset.lv; build(); refocus(keep); }
    render(); check();
  }
  function refocus(key) {
    const el = key && (box.querySelector("#" + key) || box.querySelector(`[data-lv="${key}"]`));
    if (el) { el.focus(); el.setSelectionRange?.(el.value.length, el.value.length); }
  }

  // ===== العرض =====
  function render() {
    const o = all();
    // المجموع في العنوان (يظهر حتى والخانة مصغّرة)
    let total = 0, n = 0;
    const rows = Object.keys(o).filter(s => o[s].buy && o[s].qty).map(s => {
      const price = prices[s];
      if (price == null) return `<div data-sym="${esc(s)}"><span>${esc(s)} · ${o[s].qty} سهم</span><b>—</b></div>`;
      const pnl = (price - o[s].buy) * o[s].qty; total += pnl; n++;
      return `<div data-sym="${esc(s)}"><span>${esc(s)} · ${o[s].qty} سهم @ <bdi dir="ltr">${px(o[s].buy)}</bdi></span><b class="${pnl >= 0 ? "up" : "down"}">${sgn(pnl)}${money(pnl)}</b></div>`;
    });
    const sum = $("pw-sum");
    sum.textContent = n ? `${sgn(total)}${money(total)}` : "";
    sum.className = "pw-sum " + (total > 0 ? "up" : total < 0 ? "down" : "");
    $("pw-all").innerHTML = rows.length > 1 ? rows.join("") + `<div class="t"><span>💼 مجموع صفقاتي</span><b class="${total >= 0 ? "up" : "down"}">${sgn(total)}${money(total)}</b></div>` : "";
    if (!sym || !$("pw-now")) return place();

    const p = get(), now = prices[sym] ?? null;
    $("pw-now").textContent = px(now);
    const ok = p.buy && p.qty && now != null;
    $("pw-val").textContent = ok ? money(now * p.qty) : "—";
    const el = $("pw-pnl"), card = $("pw-pnl-box");
    if (ok) {
      const pnl = (now - p.buy) * p.qty, pct = (now / p.buy - 1) * 100;
      el.textContent = `${sgn(pnl)}${money(pnl)}  (${sgn(pct)}${pct.toFixed(2)}%)`;
      card.className = "pw-pnl " + (pnl > 0 ? "up" : pnl < 0 ? "down" : "");
      const r = purify[sym];
      $("pw-pur").hidden = !(pnl > 0 && r > 0);
      if (pnl > 0 && r > 0) $("pw-pur-v").textContent = `${money(pnl * r / 100)} (${r}%)`;
    } else { el.textContent = "—"; card.className = "pw-pnl"; $("pw-pur").hidden = true; }
    for (const k in LEVELS) {
      const out = box.querySelector(`[data-out="${k}"]`), v = p[k];
      out.closest(".lv").classList.toggle("hit", !!p.hit?.[k]);
      if (!v || !p.buy) { out.textContent = ""; out.className = ""; continue; }
      const pct = (v / p.buy - 1) * 100, amt = p.qty ? (v - p.buy) * p.qty : null;
      out.textContent = `${sgn(pct)}${pct.toFixed(2)}%` + (amt != null ? ` · ${sgn(amt)}${money(amt)}` : "") + (p.hit?.[k] ? " ✓" : "");
      out.className = pct >= 0 ? "up" : "down";
    }
    // شريط من الوقف إلى الهدف مع نسبة العائد إلى المخاطرة
    const lo = p.stop1 || p.stop2, hi = p.target1 || p.target2, track = $("pw-track");
    track.hidden = !(lo && hi && hi > lo && now != null);
    if (!track.hidden) {
      const at = v => Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100)) + "%";
      $("pw-tn").style.left = at(now);
      $("pw-tb").style.left = p.buy ? at(p.buy) : "-10px";
      $("pw-lo").textContent = "🛑 " + px(lo);
      $("pw-hi").textContent = px(hi) + " 🎯";
      $("pw-rr").innerHTML = p.buy && p.buy > lo && hi > p.buy ? `العائد إلى المخاطرة <bdi dir="ltr">1 : ${((hi - p.buy) / (p.buy - lo)).toFixed(2)}</bdi>` : "";
    }
    place();
  }

  // ===== تنبيه مرة واحدة عند لمس وقف أو هدف (لكل الصفقات، في أي صفحة) =====
  function check() {
    const o = all();
    let changed = false;
    for (const [s, p] of Object.entries(o)) {
      const now = prices[s];
      if (now == null) continue;
      for (const [k, lv] of Object.entries(LEVELS)) {
        const v = p[k];
        if (!v || p.hit?.[k] || !(k.startsWith("stop") ? now <= v : now >= v)) continue;
        p.hit = { ...(p.hit || {}), [k]: Date.now() }; changed = true;
        const text = `${s} لمس ${lv.label} ${px(v)} — السعر الآن ${px(now)}`;
        try { const c = new AudioContext(), osc = c.createOscillator(), g = c.createGain(); osc.frequency.value = k.startsWith("stop") ? 440 : 990; g.gain.value = 0.1; osc.connect(g); g.connect(c.destination); osc.start(); osc.stop(c.currentTime + 0.5); } catch {}
        if ("Notification" in window && Notification.permission === "granted") new Notification(`${lv.icon} ${lv.label} — ${s}`, { body: text });
      }
    }
    if (changed) { save(KEY, o); window.dispatchEvent(new CustomEvent("positions-changed")); render(); }
  }

  box.addEventListener("focusin", () => { if ("Notification" in window && Notification.permission === "default") Notification.requestPermission(); }, { once: true });
  // صفحة أخرى (تبويب آخر) عدّلت الصفقات
  addEventListener("storage", e => { if (e.key === KEY) { build(); window.dispatchEvent(new CustomEvent("positions-changed")); } });

  window.PositionWidget = {
    levels: LEVELS,
    get: s => all()[s] || null,
    price(s, p) { if (p != null) { prices[s] = p; pushed[s] = Date.now(); if (s === sym) render(); check(); } },  // صفحة السهم تمرر سعرها اللحظي
  };
  build(); place(); refresh();
  window.dispatchEvent(new CustomEvent("positions-changed"));  // صفحة السهم ترسم خطوطها الآن
  setInterval(refresh, 4000);
})();
