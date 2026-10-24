// 🔴 عاجل: نغمة أخبار عاجلة (مولّدة في المتصفح بأسلوب نشرات الأخبار) + شريط تنبيه، عند ظهور إعلان مهم جديد.
// المهم: شطب من البورصة، أو خبر عاجل (إشعار بورصة، إفلاس، طرح أسهم، استحواذ، تقسيم عكسي)، أو أي إعلان عن سهم في صفقاتك أو مفضلتك.
(function () {
  const SEEN = "breaking_seen", SOUND = "breaking_sound";
  const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
  const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} };
  let ctx = null;

  // المتصفح لا يسمح بالصوت قبل أول ضغطة في الصفحة: نجهّز الصوت عندها
  const unlock = () => {
    try { ctx = ctx || new (window.AudioContext || window.webkitAudioContext)(); ctx.resume(); } catch {}
  };
  ["pointerdown", "keydown"].forEach(e => window.addEventListener(e, unlock, { once: true, capture: true }));

  function tone(type, freq, t, dur, vol, out, glideTo) {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type; o.frequency.setValueAtTime(freq, t);
    if (glideTo) o.frequency.exponentialRampToValueAtTime(glideTo, t + dur);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(vol, t + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(out); o.start(t); o.stop(t + dur + 0.05);
  }

  // ضربة منخفضة + اندفاع هواء + ثلاث ضربات نحاسية متصاعدة ثم وتر ممتد
  function play() {
    try {
      unlock();
      if (!ctx) return;
      const t0 = ctx.currentTime + 0.05;
      const master = ctx.createGain(); master.gain.value = 0.55; master.connect(ctx.destination);
      const brass = ctx.createBiquadFilter(); brass.type = "lowpass"; brass.frequency.value = 2200; brass.Q.value = 0.8; brass.connect(master);

      tone("sine", 110, t0, 1.1, 0.9, master, 38);  // الضربة
      const len = ctx.sampleRate * 0.6, buf = ctx.createBuffer(1, len, ctx.sampleRate), d = buf.getChannelData(0);
      for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 2);
      const n = ctx.createBufferSource(), nf = ctx.createBiquadFilter(), ng = ctx.createGain();
      n.buffer = buf; nf.type = "bandpass"; nf.frequency.setValueAtTime(400, t0); nf.frequency.exponentialRampToValueAtTime(3000, t0 + 0.5);
      ng.gain.value = 0.25; n.connect(nf); nf.connect(ng); ng.connect(master); n.start(t0);

      const chord = (notes, t, dur, vol) => notes.forEach(f => { tone("sawtooth", f, t, dur, vol, brass); tone("square", f / 2, t, dur, vol * 0.4, brass); });
      chord([146.8, 174.6, 220.0], t0 + 0.10, 0.22, 0.07);   // ري صغير
      chord([146.8, 174.6, 220.0], t0 + 0.38, 0.22, 0.07);
      chord([174.6, 220.0, 261.6], t0 + 0.66, 0.22, 0.08);   // فا
      chord([220.0, 277.2, 329.6, 440.0], t0 + 0.95, 1.6, 0.075);  // لا (وتر ممتد)
      tone("sine", 55, t0 + 0.95, 1.6, 0.5, master);
    } catch {}
  }

  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  function toast(list) {
    let box = document.getElementById("breaking-toast");
    if (!box) {
      box = document.createElement("div"); box.id = "breaking-toast";
      box.style.cssText = "position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:9999;max-width:min(760px,94vw);background:#7f1d1d;border:1px solid #ef4444;color:#fff;border-radius:10px;box-shadow:0 10px 30px rgba(0,0,0,.5);font-size:14px;overflow:hidden";
      document.body.appendChild(box);
    }
    box.innerHTML = `<div style="display:flex;align-items:stretch"><b style="background:#dc2626;padding:10px 14px;white-space:nowrap;animation:brk-blink 1s steps(2) infinite">🔴 عاجل</b>` +
      `<div style="padding:8px 12px;display:flex;flex-direction:column;gap:3px">${list.slice(0, 3).map(i => `<a href="${esc(i.href)}" ${i.href.startsWith("http") ? 'target="_blank" rel="noopener"' : ""} style="color:#fff">${i.html}</a>`).join("")}` +
      `${list.length > 3 ? `<span style="opacity:.75;font-size:12px">و ${list.length - 3} إعلانات أخرى في الشريط</span>` : ""}</div>` +
      `<button type="button" style="margin-inline-start:auto;background:none;border:0;color:#fff;font-size:18px;cursor:pointer;padding:0 12px" title="إغلاق">×</button></div>`;
    box.querySelector("button").onclick = () => box.remove();
    clearTimeout(box._t); box._t = setTimeout(() => box.remove(), 15000);
  }
  if (!document.getElementById("brk-style")) {
    const st = document.createElement("style"); st.id = "brk-style";
    st.textContent = "@keyframes brk-blink { 50% { background:#991b1b; } }";
    document.head.appendChild(st);
  }

  window.BreakingNews = {
    enabled: () => load(SOUND, true),
    toggle() { save(SOUND, !this.enabled()); unlock(); if (this.enabled()) play(); return this.enabled(); },
    test: play,
    // entries: [{id, important, html, href, title}] — يُنبَّه فقط على الجديد منها، وأول تحميل يُسجَّل بلا صوت
    check(entries) {
      const seen = load(SEEN, null), first = seen === null, known = new Set(seen || []);
      const fresh = entries.filter(e => e.important && !known.has(e.id));
      save(SEEN, [...new Set([...(seen || []), ...entries.map(e => e.id)])].slice(-600));
      if (first || !fresh.length) return;
      toast(fresh);
      if (this.enabled()) play();
      if ("Notification" in window && Notification.permission === "granted")
        fresh.slice(0, 3).forEach(e => new Notification("🔴 عاجل", { body: e.title }));
    },
  };
})();
