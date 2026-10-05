# -*- coding: utf-8 -*-
"""
dashboard.py — لوحة التحكم (صفحة الويب)
========================================

خادم Flask صغير يعرض نتائج البوت في المتصفح على http://localhost:8000

كيف يعمل؟
  - البوت (bot.py) يكتب النتائج في output/latest.json بعد كل تحليل.
  - هذه اللوحة تقرأ ذلك الملف وترسله للمتصفح.
  - الصفحة (templates/dashboard.html) تفحص كل بضع ثوانٍ هل يوجد تحديث جديد،
    وتعيد التحميل الكامل كل 5 دقائق (حسب dashboard_refresh في config.yaml).

التشغيل:
  - تلقائيًا مع:  python bot.py
  - أو منفصلًا:   python dashboard.py   (يعرض آخر نتائج كتبها البوت)

المسارات (Routes):
  /                  ← الصفحة الرئيسية
  /api/data          ← كل البيانات بصيغة JSON
  /api/version       ← رقم آخر تحديث (للفحص السريع)
  /plotly.min.js     ← مكتبة الرسوم البيانية (من حزمة plotly المثبتة — تعمل بدون إنترنت)
  /download/excel    ← تنزيل signals.xlsx
  /download/alerts   ← تنزيل alerts.log
  /api/import        ← إدخال بيانات (لصق نص أو رفع ملف) ثم التحليل فورًا
  /api/clear-data    ← حذف كل ملفات البيانات (مثل البيانات التجريبية)
"""

from __future__ import annotations

import logging
import os
import threading
import webbrowser
from functools import lru_cache

from flask import Flask, Response, abort, jsonify, render_template, request, send_file

from bot import (BASE_DIR, FIELD_ALIASES, FILE_LABELS, StockBot, clear_data_folder, custom_file_name,
                 detect_file_key, find_column, is_sample_data, norm_header,
                 load_config, match_fields, parse_table_text, parse_uploaded_file, resolve_path,
                 save_data_file)

log = logging.getLogger("stock_bot.dashboard")


@lru_cache(maxsize=1)
def plotly_js() -> str:
    """مكتبة Plotly.js من حزمة plotly المثبتة (تُقرأ مرة واحدة فقط)."""
    from plotly.offline import get_plotlyjs
    return get_plotlyjs()


def create_app(cfg: dict | None = None, bot: StockBot | None = None) -> Flask:
    """
    إنشاء تطبيق Flask بالإعدادات المعطاة.
    bot: البوت الذي يعيد التحليل بعد إدخال بيانات من الصفحة
         (إذا شُغّلت اللوحة وحدها عبر python dashboard.py يُنشأ بوت داخلي).
    """
    cfg = cfg or load_config()
    bot = bot or StockBot(cfg)
    app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
    app.json.ensure_ascii = False                     # إظهار العربية كما هي في JSON
    app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024   # أقصى حجم للرفع: 30 ميجا

    json_path = resolve_path(cfg["output"]["json_file"])
    excel_path = resolve_path(cfg["output"]["excel_file"])
    alerts_path = resolve_path(cfg["output"]["alerts_file"])

    def read_payload_bytes() -> bytes | None:
        """قراءة latest.json مع إعادة المحاولة إذا كان البوت يكتبه في نفس اللحظة."""
        for _ in range(5):
            try:
                with open(json_path, "rb") as fh:
                    return fh.read()
            except FileNotFoundError:
                return None
            except PermissionError:
                threading.Event().wait(0.2)
        return None

    @app.route("/")
    def index():
        return render_template(
            "dashboard.html",
            refresh_seconds=int(cfg["output"]["dashboard_refresh"]),
            poll_seconds=int(cfg["output"]["dashboard_poll"]),
            file_types=[(key, FILE_LABELS.get(key, key), fname)
                        for key, fname in cfg["data"]["files"].items()],
        )

    @app.route("/api/data")
    def api_data():
        data = read_payload_bytes()
        if data is None:
            return jsonify({"empty": True, "message": "لا توجد نتائج بعد — شغّل bot.py وتأكد من وجود ملفات CSV في مجلد data"})
        resp = Response(data, mimetype="application/json; charset=utf-8")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/api/version")
    def api_version():
        # نستخدم وقت تعديل الملف كرقم إصدار (سريع ولا يحتاج قراءة الملف)
        try:
            version = os.path.getmtime(json_path)
        except OSError:
            version = 0
        resp = jsonify({"version": version})
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/plotly.min.js")
    def plotly_route():
        resp = Response(plotly_js(), mimetype="application/javascript")
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    @app.route("/download/excel")
    def download_excel():
        if not excel_path.exists():
            abort(404)
        return send_file(excel_path, as_attachment=True, download_name=excel_path.name)

    @app.route("/download/alerts")
    def download_alerts():
        if not alerts_path.exists():
            abort(404)
        return send_file(alerts_path, as_attachment=True, download_name=alerts_path.name,
                         mimetype="text/plain; charset=utf-8")

    @app.route("/api/import", methods=["POST"])
    def api_import():
        """
        إدخال بيانات من الصفحة. يقبل:
          - text:   نص ملصوق (منسوخ من تكرتشارت أو Excel)
          - file:   ملف مرفوع (CSV / TXT / XLSX)
          - kind:   نوع البيانات (sahmak / liquidity / ...) أو auto للتحديد التلقائي
          - append: 1 لإضافة الصفوف للملف الموجود بدل استبداله
          - preview: 1 للمعاينة فقط بدون حفظ
        """
        kind = request.form.get("kind", "auto")
        append = request.form.get("append") == "1"
        preview = request.form.get("preview") == "1"
        upload = request.files.get("file")
        filename = upload.filename if upload else ""
        try:
            if upload:
                df = parse_uploaded_file(filename, upload.read(), cfg["data"]["encodings"])
            else:
                text = request.form.get("text", "")
                if not text.strip():
                    return jsonify({"ok": False, "error": "الصق البيانات أولًا"}), 400
                df = parse_table_text(text)
        except Exception as exc:
            return jsonify({"ok": False, "file": filename, "error": f"تعذرت قراءة البيانات: {exc}"}), 400

        # النوع: معروف (sahmak / market / ...) أو "custom" = يُحفظ كما هو بكل أعمدته
        if kind == "auto":
            kind = detect_file_key(df, cfg, filename) or "custom"
        elif kind != "custom" and kind not in cfg["data"]["files"]:
            return jsonify({"ok": False, "error": "نوع بيانات غير معروف"}), 400

        if kind == "custom":
            target, label = custom_file_name(filename), "بيانات أخرى (كل الأعمدة كما هي)"
            # الأعمدة التي يعرفها البوت (تُستخدم في الفلاتر)؛ البقية تُحفظ وتُعرض كما هي
            known = {norm_header(a) for names in FIELD_ALIASES(cfg).values() for a in names}
            found = [c for c in df.columns if norm_header(c) in known]
            missing = []
        else:
            target, label = cfg["data"]["files"][kind], FILE_LABELS.get(kind, kind)
            found, missing = match_fields(df, kind, cfg)

        warnings_ = []
        if not find_column(df, cfg["data"]["symbol_column"]):
            warnings_.append(f"لا يوجد عمود \"{cfg['data']['symbol_column']}\" — لن يُعرف لأي سهم تنتمي الصفوف"
                             " (إلا إذا كانت لسهم واحد فقط)")
        result = {"ok": True, "file": filename, "kind": kind, "label": label,
                  "target": target, "rows": len(df), "columns": list(df.columns),
                  "found": found, "missing": missing, "warnings": warnings_, "saved": False,
                  "preview": [[("" if v is None or v != v else str(v)) for v in row]
                              for row in df.head(5).itertuples(index=False, name=None)]}
        if preview:
            return jsonify(result)
        was_sample = is_sample_data(cfg)
        try:
            result["total_rows"] = save_data_file(df, kind, cfg, append,
                                                  target if kind == "custom" else None)
        except Exception as exc:
            return jsonify({**result, "ok": False, "error": str(exc)}), 500
        result["saved"] = True
        if was_sample:
            bot.reset_alerts()                    # تنبيهات البيانات التجريبية لا تخص البيانات الحقيقية
        if request.form.get("analyze", "1") == "1":
            bot.run_safe()                        # تحليل فوري بعد الحفظ
        return jsonify(result)

    @app.route("/api/analyze", methods=["POST"])
    def api_analyze():
        bot.run_safe()
        return jsonify({"ok": True})

    @app.route("/api/clear-data", methods=["POST"])
    def api_clear():
        removed = clear_data_folder(cfg)
        bot.reset_alerts()
        bot.run_safe()
        return jsonify({"ok": True, "removed": removed})

    return app


if __name__ == "__main__":
    # تشغيل اللوحة وحدها (بدون البوت) لعرض آخر نتائج محفوظة
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")
    config = load_config()
    host = config["output"]["dashboard_host"]
    port = int(config["output"]["dashboard_port"])
    url = f"http://localhost:{port}"
    print(f"🌐 لوحة التحكم: {url}  (Ctrl+C للإيقاف)")
    if config["output"]["open_browser"]:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    create_app(config).run(host=host, port=port, debug=False, threaded=True)
