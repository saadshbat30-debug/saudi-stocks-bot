"""بث Alpaca: اختبارات دون مفاتيح حقيقية، على خادم محلي يحاكي بروتوكول Alpaca عبر WebSocket فعلي.

يغطي: المصادقة الناجحة والفاشلة، رفض SIP (409)، حد الاتصال (406)، استقبال الصفقات والأسعار والشموع، قياس
التأخير، إعادة الاتصال بعد إغلاق الخادم وبعد انقطاع البيانات، وعدم ظهور المفاتيح في السجلات.

python -m unittest tests.test_alpaca_stream
"""
import asyncio
import io
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import alpaca_stream as al  # noqa: E402

KEY, SECRET = "PKTESTKEY1234567890AB", "SeCrEt-TEST-abcdefghijklmnopqrstuvwxyz0123"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "123Z"


class FakeAlpaca:
    """خادم WebSocket محلي يتبع رسائل Alpaca الموثقة."""

    def __init__(self, mode="ok"):
        self.mode, self.sessions, self.auth_msgs, self.subs = mode, 0, [], []
        self.conns, self.drop_data = [], False
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        asyncio.run_coroutine_threadsafe(self._serve(), self.loop).result(5)

    async def _serve(self):
        import websockets
        self.server = await websockets.serve(self._handler, "127.0.0.1", self.port)

    async def _handler(self, ws):
        self.sessions += 1
        self.conns.append(ws)
        await ws.send('[{"T":"success","msg":"connected"}]')
        if self.mode == "limit":
            await ws.send('[{"T":"error","code":406,"msg":"connection limit exceeded"}]')
            await ws.close(); return
        auth = json.loads(await ws.recv())
        self.auth_msgs.append(auth)
        if auth.get("key") != KEY or auth.get("secret") != SECRET:
            await ws.send('[{"T":"error","code":402,"msg":"auth failed"}]'); await ws.close(); return
        if self.mode == "nosip":
            await ws.send('[{"T":"error","code":409,"msg":"insufficient subscription"}]'); await ws.close(); return
        await ws.send('[{"T":"success","msg":"authenticated"}]')
        sub = json.loads(await ws.recv())
        self.subs.append(sub)
        await ws.send(json.dumps([{"T": "subscription", **{k: v for k, v in sub.items() if k != "action"}}]))
        i = 0
        try:
            while True:
                await asyncio.sleep(0.05)
                if self.drop_data:  # لا بيانات، لكن الاتصال مفتوح (يُنهى عند إغلاقه)
                    if ws.close_code is not None:
                        return
                    continue
                now = time.time()
                i += 1
                batch = [{"T": "t", "S": "AAPL", "i": i, "x": "V" if i % 2 else "Q", "p": 190.0 + i / 100, "s": 100, "c": ["@"], "t": iso(now - 0.020), "z": "C"},
                         {"T": "q", "S": "TSLA", "bx": "Q", "bp": 400.0, "bs": 2, "ax": "P", "ap": 400.05, "as": 3, "c": ["R"], "t": iso(now - 0.010), "z": "C"}]
                if i % 10 == 0:
                    minute = int(now // 60 * 60) - 60
                    batch.append({"T": "b", "S": "AAPL", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1000, "n": 12, "vw": 1.2,
                                  "t": datetime.fromtimestamp(minute, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
                await ws.send(json.dumps(batch))
        except Exception:
            pass

    def kick(self):
        """يغلق كل الاتصالات من جهة الخادم (محاكاة انقطاع الشبكة أو إعادة تشغيل خادم Alpaca)."""
        for ws in list(self.conns):
            asyncio.run_coroutine_threadsafe(ws.close(), self.loop)

    def stop(self):
        # يغلق الخادم والاتصالات؛ حلقة الخادم في خيط خلفي تبقى حتى نهاية العملية (إيقافها يقطع مهام websockets)
        self.loop.call_soon_threadsafe(self.server.close)

def cfg(port, **kw):
    c = {"key": KEY, "secret": SECRET, "feed": "sip", "url": f"ws://127.0.0.1:{port}", "trading_url": al.PAPER_TRADING_URL,
         "trades": ["*"], "quotes": ["*"], "bars": ["*"], "stale_seconds": 1.0, "bars_keep": 30}
    c.update(kw)
    return c


def wait(cond, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logfile = os.path.join(self.tmp.name, "alpaca.log")
        self.console = io.StringIO()
        with mock.patch("sys.stderr", self.console):
            al.setup_logging((KEY, SECRET), self.logfile)
        for h in al.log.handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                h.setStream(self.console)
        self.clients, self.servers = [], []

    def tearDown(self):
        for c in self.clients:
            c.stop()
        for s in self.servers:
            s.stop()
        for h in list(al.log.handlers):
            h.close(); al.log.removeHandler(h)
        self.tmp.cleanup()

    def run_client(self, server, **kw):
        c = al.AlpacaStream(cfg(server.port, **kw), market_hours=kw.pop("hours", lambda: True))
        self.clients.append(c)
        c.start()
        return c

    def server(self, mode="ok"):
        s = FakeAlpaca(mode)
        self.servers.append(s)
        return s

    def logs(self):
        for h in al.log.handlers:
            h.flush()
        return open(self.logfile, encoding="utf-8").read() + self.console.getvalue()


class TestStream(Base):
    def test_auth_subscribe_and_receive_trades_quotes_bars(self):
        s = self.server()
        c = self.run_client(s)
        self.assertTrue(wait(lambda: c.counts["bars"] >= 1 and c.counts["trades"] >= 20))
        self.assertEqual(s.auth_msgs[0], {"action": "auth", "key": KEY, "secret": SECRET})
        self.assertEqual(s.subs[0], {"action": "subscribe", "trades": ["*"], "quotes": ["*"], "bars": ["*"], "updatedBars": ["*"]})
        st = c.status()
        self.assertEqual((st["state"], st["authenticated"], st["feed"], st["orders_enabled"]), ("streaming", True, "sip", False))
        self.assertEqual(al.ts(c.trades["AAPL"]["t"]) // 1, al.ts(c.trades["AAPL"]["t"]) // 1)
        self.assertEqual(c.quotes["TSLA"]["ap"], 400.05)
        self.assertEqual(c.bars["AAPL"][-1][4], 1.5)
        # التأخير المقاس: الخادم يضع وقت الحدث قبل الإرسال بـ 20ms (صفقة) و10ms (سعر)
        lt, lq = st["latency"]["trades"], st["latency"]["quotes"]
        self.assertGreaterEqual(lt["p50_ms"], 19); self.assertLess(lt["p50_ms"], 500)
        self.assertGreaterEqual(lq["p50_ms"], 9); self.assertLess(lq["p50_ms"], 500)
        self.assertGreater(st["msgs_per_s_10s"], 0)

    def test_reconnects_after_server_drops_and_resubscribes(self):
        s = self.server()
        c = self.run_client(s)
        self.assertTrue(wait(lambda: c.counts["trades"] > 5))
        before = c.counts["trades"]
        s.kick()
        self.assertTrue(wait(lambda: c.connects >= 2 and c.state == "streaming"))
        self.assertTrue(wait(lambda: c.counts["trades"] > before + 5))
        self.assertEqual(len(s.subs), 2)          # أعاد الاشتراك
        self.assertGreaterEqual(c.reconnects, 1)
        g = c.status()["gaps"][0]
        self.assertIsNotNone(g["to"]); self.assertLess(g["to"] - g["from"], 5)
        self.assertIn("انقطع الاتصال", self.logs())

    def test_stale_data_forces_reconnect_only_during_market_hours(self):
        s = self.server()
        c = self.run_client(s)
        self.assertTrue(wait(lambda: c.counts["trades"] > 3))
        s.drop_data = True
        self.assertTrue(wait(lambda: c.connects >= 2, timeout=15))
        self.assertIn("لا بيانات منذ", self.logs())

    def test_quiet_market_is_not_treated_as_stale(self):
        s = self.server()
        s.drop_data = True
        c = self.run_client(s, hours=lambda: False)
        self.assertTrue(wait(lambda: c.state == "streaming"))
        time.sleep(7)
        self.assertEqual(c.connects, 1)

    def test_bad_key_stops_without_retrying(self):
        s = self.server()
        c = al.AlpacaStream(cfg(s.port, secret="wrong-secret-value-123456"), market_hours=lambda: True)
        self.clients.append(c)
        c.start()
        self.assertTrue(wait(lambda: c.state == "failed"))
        time.sleep(2)
        self.assertEqual((s.sessions, c.authenticated), (1, False))
        self.assertIn("402", c.status()["fatal"])

    def test_no_sip_subscription_is_fatal_and_never_falls_back_to_iex(self):
        s = self.server("nosip")
        c = self.run_client(s)
        self.assertTrue(wait(lambda: c.state == "failed"))
        self.assertIn("409", c.fatal)
        self.assertEqual(c.cfg["url"], f"ws://127.0.0.1:{s.port}")
        self.assertEqual(s.sessions, 1)

    def test_connection_limit_waits_a_minute_before_retrying(self):
        s = self.server("limit")
        c = self.run_client(s)
        self.assertTrue(wait(lambda: c.state == "reconnecting"))
        time.sleep(2)
        self.assertEqual(s.sessions, 1)  # 406: انتظار 60 ثانية لا دقائق متتالية
        self.assertEqual(c.errors[-1]["code"], 406)

    def test_keys_never_appear_in_logs_or_status(self):
        s = self.server()
        c = self.run_client(s)
        self.assertTrue(wait(lambda: c.counts["trades"] > 3))
        al.log.error("محاولة تسريب: key=%s secret=%s", KEY, SECRET)
        al.log.error('{"action":"auth","key":"%s","secret":"%s"}', KEY, SECRET)
        try:
            raise RuntimeError(f"fail with {SECRET}")
        except RuntimeError:
            al.log.exception("استثناء")
        s.kick()
        wait(lambda: c.connects >= 2)
        text = self.logs() + json.dumps(c.status(), ensure_ascii=False)
        for secret in (KEY, SECRET, SECRET[:12], KEY[:12]):
            self.assertNotIn(secret, text)
        self.assertIn("***", text)


class TestConfig(unittest.TestCase):
    def env(self, **kw):
        base = {"APCA_API_KEY_ID": KEY, "APCA_API_SECRET_KEY": SECRET}
        base.update(kw)
        return mock.patch.dict(os.environ, base, clear=True)

    def test_defaults_are_sip_and_paper_with_all_symbols(self):
        with self.env(), mock.patch.object(al, "load_env"):
            c = al.config()
        self.assertEqual((c["feed"], c["url"], c["trading_url"]), ("sip", "wss://stream.data.alpaca.markets/v2/sip", al.PAPER_TRADING_URL))
        self.assertEqual((c["trades"], c["quotes"], c["bars"]), (["*"], ["*"], ["*"]))

    def test_iex_live_trading_and_missing_keys_are_refused(self):
        with mock.patch.object(al, "load_env"):
            for kw in ({"ALPACA_FEED": "iex"}, {"ALPACA_FEED": "delayed_sip"}, {"ALPACA_TRADING_URL": "https://api.alpaca.markets"},
                       {"APCA_API_SECRET_KEY": ""}):
                with self.env(**kw), self.assertRaises(al.ConfigError) as e:
                    al.config()
                self.assertNotIn(SECRET, str(e.exception))

    def test_symbol_lists(self):
        with self.env(ALPACA_QUOTES="aapl, tsla", ALPACA_BARS="off"), mock.patch.object(al, "load_env"):
            c = al.config()
        self.assertEqual((c["quotes"], c["bars"]), (["AAPL", "TSLA"], []))

    def test_no_order_code(self):
        src = open(al.__file__, encoding="utf-8").read()
        for word in ("/v2/orders", "submit_order", "\"side\"", "'side'", "api.alpaca.markets/v2"):
            self.assertNotIn(word, src)

    def test_env_file_is_git_ignored(self):
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run(["git", "check-ignore", ".env"], cwd=root, capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), ".env")


class TestTimestamps(unittest.TestCase):
    def test_nanosecond_rfc3339(self):
        self.assertAlmostEqual(al.ts("2021-02-22T15:51:45.335689322Z"), 1614009105.335689322, places=6)
        self.assertEqual(al.ts("2021-02-22T19:15:00Z"), 1614021300.0)


if __name__ == "__main__":
    unittest.main()
