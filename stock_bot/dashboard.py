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
"""

from __future__ import annotations

import logging
import os
import threading
import webbrowser
from functools import lru_cache

from flask import Flask, Response, abort, jsonify, render_template, send_file

from bot import BASE_DIR, load_config, resolve_path

log = logging.getLogger("stock_bot.dashboard")


@lru_cache(maxsize=1)
def plotly_js() -> str:
    """مكتبة Plotly.js من حزمة plotly المثبتة (تُقرأ مرة واحدة فقط)."""
    from plotly.offline import get_plotlyjs
    return get_plotlyjs()


def create_app(cfg: dict | None = None) -> Flask:
    """إنشاء تطبيق Flask بالإعدادات المعطاة."""
    cfg = cfg or load_config()
    app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
    app.json.ensure_ascii = False                     # إظهار العربية كما هي في JSON

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
