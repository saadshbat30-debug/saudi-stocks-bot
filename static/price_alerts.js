// تنبيهات السعر: "نبهني إذا وصل السهم إلى سعر معين" — تُحفظ في المتصفح وتعمل في كل الصفحات.
(function () {
  const KEY = "price_alerts";
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch { return []; } };
  const save = list => { try { localStorage.setItem(KEY, JSON.stringify(list)); } catch {} };

  window.PriceAlerts = {
    all: load,
    forSymbol: symbol => load().filter(a => a.symbol === symbol),
    // الاتجاه يُحدد تلقائيًا: فوق السعر الحالي = اختراق للأعلى، تحته = كسر للأسفل
    add(symbol, price, current) {
      const list = load();
      const a = { id: Date.now() + Math.random().toString(36).slice(2, 6), symbol, price, dir: current != null && price < current ? "below" : "above", created: Date.now() };
      list.push(a); save(list);
      return a;
    },
    remove(id) { save(load().filter(a => a.id !== id)); },
    // quotes: { رمز: سعر } — يرجع التنبيهات التي تحققت ويحذفها
    check(quotes) {
      const list = load(), hit = [], keep = [];
      for (const a of list) {
        const p = quotes[a.symbol];
        if (p != null && (a.dir === "above" ? p >= a.price : p <= a.price)) hit.push({ ...a, hitPrice: p });
        else keep.push(a);
      }
      if (hit.length) save(keep);
      return hit;
    },
  };
})();
