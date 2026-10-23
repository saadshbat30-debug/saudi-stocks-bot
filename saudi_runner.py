"""تشغيل منصة أبو طلال للسوق السعودي (tasi-intel) تلقائيًا مع هذه المنصة في نفس النافذة.

تبحث عن مجلد tasi-intel (SAUDI_DIR، أو ~/tasi-intel، أو بجانب هذا المجلد)، وتشغّل خادمه
بنفس إعداداته (local.env وبيئته .venv) على المنفذ 8090، وتطبع رسائله هنا مسبوقة بـ [السعودي]،
وتوقفه عند إغلاق هذه المنصة. لا تغيّر شيئًا في ملفات tasi-intel.
"""
import atexit
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

PORT = 8090


def _find_dir():
    here = Path(__file__).resolve().parent
    for p in (os.environ.get("SAUDI_DIR"), Path.home() / "tasi-intel", here.parent / "tasi-intel"):
        if p and (Path(p) / "server" / "main.py").exists():
            return Path(p)
    return None


def _port_busy(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _read_env(path):
    env = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def _pipe(proc):
    for raw in iter(proc.stdout.readline, b""):
        line = raw.decode("utf-8", errors="replace").rstrip()
        if line:
            print(f"[السعودي] {line}", flush=True)


def start():
    """يشغّل منصة السوق السعودي إن وُجدت ولم تكن تعمل. يرجع رسالة توضح ما حدث."""
    if os.environ.get("SAUDI_AUTOSTART", "1") == "0":
        return None
    folder = _find_dir()
    if not folder:
        return None  # المنصة السعودية غير مثبتة على هذا الجهاز
    if _port_busy(PORT):
        return f"🇸🇦 منصة السوق السعودي تعمل مسبقًا على http://127.0.0.1:{PORT}"
    py = folder / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env_file = folder / "local.env"
    settings = _read_env(env_file)
    if not py.exists() or not settings.get("SAHMK_API_KEY"):
        # أول تشغيل يحتاج تثبيت المكتبات وإدخال مفتاح SAHMK من سكربتها مرة واحدة
        return (f"⚠️ منصة السوق السعودي تحتاج إعدادًا لمرة واحدة: افتح PowerShell وشغّل\n"
                f"   cd {folder}\n   .\\start-windows.bat\n   ثم أغلقها وأعد تشغيل هذه المنصة")
    env = {**os.environ, **settings, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(
        [str(py), "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1", "--port", str(PORT), "--workers", "1", "--no-access-log"],
        cwd=str(folder), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags,
    )
    threading.Thread(target=_pipe, args=(proc,), daemon=True).start()

    def stop():
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    atexit.register(stop)
    return f"🇸🇦 منصة السوق السعودي تعمل أيضًا: http://127.0.0.1:{PORT} (من {folder})"


if __name__ == "__main__":
    print(start() or "لم يُعثر على مجلد tasi-intel")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        sys.exit(0)
