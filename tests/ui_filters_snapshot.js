// يسجّل ترتيب الأسهم الظاهرة في الصفحة الرئيسية لكل تبويب/سوق/جلسة/نطاق سعري على بيانات ثابتة.
// الاستخدام: node tests/ui_filters_snapshot.js <رابط المنصة> <ملف الصفوف JSON> <ملف الناتج>
// يحتاج Playwright. يُشغَّل قبل التعديل وبعده، ويجب أن يتطابق الناتجان.
const { chromium } = require("playwright");
const fs = require("fs");
(async () => {
  const [url, rowsFile, out] = process.argv.slice(2);
  const rows = JSON.parse(fs.readFileSync(rowsFile, "utf8")).enriched;
  const b = await chromium.launch(), p = await b.newPage({ viewport: { width: 1600, height: 900 }, locale: "en-US" });
  const errors = [];
  p.on("pageerror", e => errors.push(e.message));
  await p.route("**/api/market/us", r => r.fulfill({ json: rows }));
  await p.route("**/api/halts**", r => r.fulfill({ json: [] }));
  await p.goto(url + "/");
  await p.evaluate(() => localStorage.clear());
  await p.reload();
  await p.waitForTimeout(2500);
  const order = () => p.$$eval("#rows tr[data-symbol]", l => l.map(x => x.dataset.symbol));
  const result = {};
  const tabs = ["all", "gainers", "losers", "active", "value", "momentum", "liquidity", "high", "lowfloat", "halted", "sharia"];
  for (const ext of ["0", "1"]) {
    await p.click(`[data-ext="${ext}"]`);
    for (const venue of ["all", "listed", "otc"]) {
      await p.click(`[data-venue="${venue}"]`);
      for (const tab of tabs) {
        await p.click(`[data-tab="${tab}"]`);
        await p.waitForTimeout(60);
        result[`ext${ext}|${venue}|${tab}`] = await order();
      }
      await p.click('[data-tab="all"]');
      for (const band of ["0", "1", "2", "3", "4", "5"]) {
        await p.click(`[data-band="${band}"]`); await p.waitForTimeout(60);
        result[`ext${ext}|${venue}|band${band}`] = await order();
      }
      await p.click('[data-band="-1"]');
      for (const sig of ["oversold", "overbought", "uptrend", "downtrend"]) {
        await p.click(`[data-sig="${sig}"]`); await p.waitForTimeout(60);
        result[`ext${ext}|${venue}|sig-${sig}`] = await order();
        await p.click(`[data-sig="${sig}"]`);
      }
    }
  }
  // بطاقات أعلى الصفحة (أقوى زخم، أعلى سيولة نسبية، الأعلى قيمة)
  result.cards = await p.$$eval(".top .card", l => l.map(c => c.innerText.replace(/\s+/g, " ").trim()));
  result.errors = errors;
  fs.writeFileSync(out, JSON.stringify(result, null, 1));
  await b.close();
  console.log("لقطات:", Object.keys(result).length - 2, "· أخطاء الصفحة:", errors.length);
})();
