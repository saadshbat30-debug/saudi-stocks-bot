// تشغيل المنصة فعليًا في المتصفح بعد الاستبدال: الرادار، صفحة السهم بكل الفواصل، فلتر قبل الانطلاق.
// الاستخدام: node tests/ui_tradingview_check.js http://127.0.0.1:5000 TSLA [مجلد_الصور]
const { chromium } = require("playwright");
(async () => {
  const [url, symbol = "TSLA", shots] = process.argv.slice(2);
  const b = await chromium.launch(), p = await b.newPage({ viewport: { width: 1600, height: 950 }, locale: "en-US" });
  const errors = [], out = {};
  p.on("pageerror", e => errors.push(e.message));
  await p.goto(url + "/");
  await p.waitForSelector("#rows tr[data-symbol]", { timeout: 90000 });
  await p.waitForTimeout(3000);
  out.radar_rows = await p.$$eval("#rows tr[data-symbol]", l => l.length);
  out.status_badge = await p.$eval("#stream-status", e => e.textContent + " | " + e.title.split("\n").slice(0, 2).join(" / "));
  out.radar_first_price_title = await p.$eval("#rows tr[data-symbol] td.num", e => e.title);
  if (shots) await p.screenshot({ path: shots + "/radar.png" });

  await p.goto(url + "/stock/" + symbol);
  await p.waitForTimeout(6000);
  out.stock_src = await p.$eval("#k-src", e => e.textContent);
  out.stock_src_title = await p.$eval("#k-src", e => e.title);
  out.intervals = {};
  for (const iv of ["1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"]) {
    await p.click(`[data-interval="${iv}"]`);
    await p.waitForTimeout(3500);
    out.intervals[iv] = await p.evaluate(() => ({
      bars: bars.length, empty: !document.getElementById("chart-empty").hidden,
      last: bars.length ? { time: bars[bars.length - 1].time, close: bars[bars.length - 1].close, closed: bars[bars.length - 1].closed } : null,
      legend: document.getElementById("legend").innerText.replace(/\s+/g, " ").slice(0, 160),
      meta: { forming: chartMeta.forming, volume_scope: chartMeta.volume_scope, gaps: chartMeta.gaps, src: chartMeta.src },
    }));
    if (shots && (iv === "5m" || iv === "1d")) await p.screenshot({ path: `${shots}/stock_${iv}.png` });
  }
  out.flow_note = await p.$eval("#f-scope", e => e.textContent);

  await p.goto(url + "/breakout");
  await p.waitForTimeout(4000);
  out.breakout_status = await p.$eval("#status", e => e.innerText.slice(0, 200));
  out.page_errors = errors;
  console.log(JSON.stringify(out, null, 1));
  await b.close();
})();
