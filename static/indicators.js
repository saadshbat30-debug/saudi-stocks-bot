// مؤشرات MACD و RSI في نوافذ أسفل الشارت اللحظي، متزامنة معه في التمرير والتكبير والمؤشر.
(function () {
  const UP = "#22c55e", DOWN = "#ef4444";

  function ema(values, n) {
    const out = new Array(values.length).fill(null), k = 2 / (n + 1);
    let prev = null, sum = 0, count = 0;
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      if (v == null) continue;
      if (prev == null) {
        sum += v; count++;
        if (count === n) { prev = sum / n; out[i] = prev; }
      } else {
        prev = v * k + prev * (1 - k); out[i] = prev;
      }
    }
    return out;
  }

  function macd(closes, fast = 12, slow = 26, signal = 9) {
    const f = ema(closes, fast), s = ema(closes, slow);
    const line = closes.map((_, i) => f[i] != null && s[i] != null ? f[i] - s[i] : null);
    const sig = ema(line, signal);
    return { line, sig, hist: line.map((v, i) => v != null && sig[i] != null ? v - sig[i] : null) };
  }

  function rsi(closes, n = 14) {
    const out = new Array(closes.length).fill(null);
    let gain = 0, loss = 0;
    for (let i = 1; i < closes.length; i++) {
      const d = closes[i] - closes[i - 1], g = Math.max(d, 0), l = Math.max(-d, 0);
      if (i <= n) {
        gain += g; loss += l;
        if (i === n) { gain /= n; loss /= n; out[i] = loss ? 100 - 100 / (1 + gain / loss) : 100; }
      } else {
        gain = (gain * (n - 1) + g) / n; loss = (loss * (n - 1) + l) / n;
        out[i] = loss ? 100 - 100 / (1 + gain / loss) : 100;
      }
    }
    return out;
  }

  const series = (bars, values) => values.map((v, i) => v == null ? { time: bars[i].time } : { time: bars[i].time, value: v });

  window.initIndicators = function ({ mainChart, getBars, container, buttons, storageKey }) {
    const opts = {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#8b98a5" },
      grid: { vertLines: { color: "#222a33" }, horzLines: { color: "#222a33" } },
      rightPriceScale: { borderColor: "#2a333d", minimumWidth: 72 },
      timeScale: { visible: false, borderColor: "#2a333d" },
      crosshair: { mode: 0 },
      handleScroll: true, handleScale: true,
    };
    // نفس عرض محور الأسعار في كل النوافذ حتى تتطابق الشموع رأسيًا
    mainChart.applyOptions({ rightPriceScale: { minimumWidth: 72 } });

    function makePane(id, title) {
      const box = document.createElement("div");
      box.className = "ind-pane"; box.hidden = true;
      box.innerHTML = `<div class="ind-label"><b>${title}</b> <span></span></div><div class="ind-chart"></div>`;
      container.appendChild(box);
      const chart = LightweightCharts.createChart(box.querySelector(".ind-chart"), opts);
      return { id, box, chart, label: box.querySelector(".ind-label span") };
    }

    // ===== MACD =====
    const m = makePane("macd", "MACD (12, 26, 9)");
    const mHist = m.chart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
    const mLine = m.chart.addLineSeries({ color: "#3b82f6", lineWidth: 1.5, priceLineVisible: false });
    const mSig = m.chart.addLineSeries({ color: "#f59e0b", lineWidth: 1.5, priceLineVisible: false });

    // ===== RSI =====
    const r = makePane("rsi", "RSI (14)");
    const rLine = r.chart.addLineSeries({ color: "#a855f7", lineWidth: 1.5, priceLineVisible: false });
    rLine.createPriceLine({ price: 70, color: DOWN, lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: "" });
    rLine.createPriceLine({ price: 30, color: UP, lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: "" });
    rLine.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) });
    r.chart.priceScale("right").applyOptions({ scaleMargins: { top: 0.06, bottom: 0.06 } });

    const panes = [m, r];
    let data = { macd: null, rsi: null };

    // ===== التزامن مع الشارت الرئيسي =====
    let syncing = false;
    const charts = [mainChart, ...panes.map(p => p.chart)];
    charts.forEach(src => src.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (syncing || !range) return;
      syncing = true;
      charts.forEach(c => { if (c !== src) c.timeScale().setVisibleLogicalRange(range); });
      syncing = false;
    }));

    function valuesAt(time) {
      const bars = getBars(), i = bars.findIndex(b => b.time === time);
      return i < 0 ? null : i;
    }
    function showLabels(i) {
      const bars = getBars();
      if (i == null) i = bars.length - 1;
      const f = v => v == null ? "—" : Math.abs(v) < 1 ? v.toFixed(4) : v.toFixed(2);
      if (data.macd) {
        const h = data.macd.hist[i];
        // كل جزء معزول باتجاهه حتى لا تنقلب الكلمة العربية مع الرقم
        const part = (name, v, c, dir = "rtl") => `<bdi dir="${dir}" style="color:${c}">${name}: <bdi dir="ltr">${f(v)}</bdi></bdi>`;
        m.label.innerHTML = `${part("MACD", data.macd.line[i], "#3b82f6", "ltr")} · ${part("الإشارة", data.macd.sig[i], "#f59e0b")} · ${part("الهيستوجرام", h, h >= 0 ? UP : DOWN)}`;
      }
      if (data.rsi) {
        const v = data.rsi[i];
        const state = v == null ? "" : v >= 70 ? ' · <span style="color:' + DOWN + '">تشبع شرائي</span>' : v <= 30 ? ' · <span style="color:' + UP + '">تشبع بيعي</span>' : "";
        r.label.innerHTML = `<span style="color:#a855f7">${v == null ? "—" : v.toFixed(1)}</span>${state}`;
      }
    }
    mainChart.subscribeCrosshairMove(p => {
      if (!p.time) { panes.forEach(x => x.chart.clearCrosshairPosition()); showLabels(null); return; }
      const i = valuesAt(p.time);
      showLabels(i);
      if (i == null) return;
      if (data.macd && data.macd.line[i] != null) m.chart.setCrosshairPosition(data.macd.line[i], p.time, mLine);
      if (data.rsi && data.rsi[i] != null) r.chart.setCrosshairPosition(data.rsi[i], p.time, rLine);
    });

    // ===== الحساب =====
    function update() {
      const bars = getBars();
      if (!bars.length) return;
      const closes = bars.map(b => b.close);
      if (!m.box.hidden) {
        data.macd = macd(closes);
        mLine.setData(series(bars, data.macd.line));
        mSig.setData(series(bars, data.macd.sig));
        mHist.setData(bars.map((b, i) => {
          const h = data.macd.hist[i];
          if (h == null) return { time: b.time };
          const prev = data.macd.hist[i - 1];
          // أخضر/أحمر حسب الإشارة، والفاتح عندما يضعف الزخم
          const color = h >= 0 ? (prev != null && h < prev ? "rgba(34,197,94,.45)" : UP) : (prev != null && h > prev ? "rgba(239,68,68,.45)" : DOWN);
          return { time: b.time, value: h, color };
        }));
      }
      if (!r.box.hidden) {
        data.rsi = rsi(closes);
        rLine.setData(series(bars, data.rsi));
      }
      const range = mainChart.timeScale().getVisibleLogicalRange();
      if (range) panes.forEach(p => p.chart.timeScale().setVisibleLogicalRange(range));
      showLabels(null);
    }

    // ===== أزرار التشغيل =====
    let enabled;
    try { enabled = JSON.parse(localStorage.getItem(storageKey)) || ["macd", "rsi"]; } catch { enabled = ["macd", "rsi"]; }
    function apply() {
      panes.forEach(p => { p.box.hidden = !enabled.includes(p.id); });
      buttons.forEach(b => b.classList.toggle("active", enabled.includes(b.dataset.ind)));
      try { localStorage.setItem(storageKey, JSON.stringify(enabled)); } catch {}
      update();
    }
    buttons.forEach(b => b.onclick = () => {
      const id = b.dataset.ind;
      enabled = enabled.includes(id) ? enabled.filter(x => x !== id) : [...enabled, id];
      apply();
    });
    apply();

    return {
      update,
      setVisible(show) { container.hidden = !show; },
    };
  };
})();
