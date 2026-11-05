package com.saad.stocks;

import android.app.Activity;
import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Vibrator;
import android.text.InputType;
import android.view.Gravity;
import android.view.Menu;
import android.view.MenuItem;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/**
 * منصة الأسهم على الجوال: تعرض صفحات المنصة التي تعمل على جهاز الكمبيوتر (py app.py) عبر نفس شبكة الواي فاي.
 * لا بيانات داخل التطبيق نفسه: كل الأسعار والشموع من خادم المنصة، وإن لم يتصل يظهر ذلك صراحة.
 */
public class MainActivity extends Activity {
    private static final int PORT = 5000;
    private static final String PREFS = "stocks";
    private static final String KEY_SERVER = "server";
    private static final String CHANNEL = "alerts";
    private static final int BG = Color.parseColor("#0f172a");
    private static final int FG = Color.parseColor("#e2e8f0");
    private static final int MUTED = Color.parseColor("#94a3b8");
    private static final int ACCENT = Color.parseColor("#22c55e");

    // يجعل تنبيهات الموقع (Notification في المتصفح) إشعارات حقيقية على الجوال
    private static final String NOTIFY_SHIM =
        "(function(){if(window.__appNotify)return;window.__appNotify=1;" +
        "function N(t,o){try{AndroidApp.notify(String(t||''),String((o&&o.body)||''));}catch(e){}}" +
        "N.permission='granted';N.requestPermission=function(cb){if(cb)cb('granted');return Promise.resolve('granted');};" +
        "window.Notification=N;})();";

    private final Handler ui = new Handler(Looper.getMainLooper());
    private SharedPreferences prefs;
    private WebView web;
    private String server;
    private int notifId = 1;
    private volatile boolean scanning;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().getDecorView().setLayoutDirection(View.LAYOUT_DIRECTION_RTL);
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        createChannel();
        askNotificationPermission();
        server = prefs.getString(KEY_SERVER, null);
        if (server == null) showSetup(null);
        else checkAndOpen(server, true);
    }

    // ===== شاشة الإعداد: البحث عن المنصة في الشبكة أو إدخال العنوان =====
    private EditText addressInput;
    private TextView statusText;
    private ProgressBar progress;
    private Button scanButton;

    private void showSetup(String message) {
        web = null;
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(BG);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(20);
        box.setPadding(pad, pad, pad, pad);
        scroll.addView(box);

        TextView title = text("📊 منصة الأسهم", 24, FG);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        box.addView(title);
        box.addView(text("التطبيق يعرض منصتك التي تعمل على جهاز الكمبيوتر.\n" +
            "• شغّل المنصة على الكمبيوتر (py app.py)\n" +
            "• اجعل الجوال والكمبيوتر على نفس شبكة الواي فاي\n" +
            "• اضغط «بحث تلقائي» أو اكتب عنوان الكمبيوتر يدويًا", 15, MUTED), lp(dp(10)));

        addressInput = new EditText(this);
        addressInput.setHint("مثال: 192.168.1.10:5000");
        addressInput.setHintTextColor(MUTED);
        addressInput.setTextColor(FG);
        addressInput.setTextDirection(View.TEXT_DIRECTION_LTR);
        addressInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        addressInput.setSingleLine(true);
        if (server != null) addressInput.setText(server);
        box.addView(addressInput, lp(dp(16)));

        Button connect = button("اتصال");
        connect.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                String a = normalize(addressInput.getText().toString());
                if (a == null) { status("اكتب عنوان الكمبيوتر، مثل 192.168.1.10:5000"); return; }
                checkAndOpen(a, false);
            }
        });
        box.addView(connect, lp(dp(8)));

        scanButton = button("🔍 بحث تلقائي في الشبكة");
        scanButton.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) { scan(); }
        });
        box.addView(scanButton, lp(dp(8)));

        progress = new ProgressBar(this);
        progress.setVisibility(View.GONE);
        box.addView(progress, lp(dp(12)));
        statusText = text(message == null ? "" : message, 15, FG);
        box.addView(statusText, lp(dp(8)));

        box.addView(text("عنوان الكمبيوتر في ويندوز: افتح PowerShell واكتب ipconfig، ثم خذ رقم IPv4 Address لشبكة الواي فاي.\n" +
            "إن لم يجده البحث: اسمح للمنفذ 5000 في جدار حماية ويندوز (الشرح في ملف android/README.md).", 13, MUTED), lp(dp(20)));
        setContentView(scroll);
    }

    private void status(final String s) {
        ui.post(new Runnable() { public void run() { if (statusText != null) statusText.setText(s); } });
    }

    private void busy(final boolean on) {
        ui.post(new Runnable() {
            public void run() {
                if (progress != null) progress.setVisibility(on ? View.VISIBLE : View.GONE);
                if (scanButton != null) scanButton.setEnabled(!on);
            }
        });
    }

    /** يبحث في شبكة الواي فاي الحالية (/24) عن جهاز يرد على /api/status بنفس شكل رد المنصة. */
    private void scan() {
        if (scanning) return;
        scanning = true;
        busy(true);
        status("جارٍ البحث في الشبكة…");
        new Thread(new Runnable() {
            public void run() {
                try {
                    List<String> prefixes = localPrefixes();
                    if (prefixes.isEmpty()) { status("الجوال غير متصل بشبكة واي فاي محلية"); return; }
                    final AtomicReference<String> found = new AtomicReference<String>();
                    final AtomicInteger checked = new AtomicInteger();
                    ExecutorService pool = Executors.newFixedThreadPool(48);
                    for (String prefix : prefixes) {
                        for (int i = 1; i < 255; i++) {
                            final String host = prefix + i + ":" + PORT;
                            pool.execute(new Runnable() {
                                public void run() {
                                    if (found.get() != null) return;
                                    if (isPlatform(host, 600, 900)) found.compareAndSet(null, host);
                                    int n = checked.incrementAndGet();
                                    if (n % 25 == 0 && found.get() == null) status("جارٍ البحث… فُحص " + n + " عنوانًا");
                                }
                            });
                        }
                    }
                    pool.shutdown();
                    pool.awaitTermination(60, TimeUnit.SECONDS);
                    final String host = found.get();
                    if (host == null) {
                        status("لم يُعثر على المنصة في الشبكة (" + checked.get() + " عنوانًا).\n" +
                            "تأكد أن المنصة تعمل على الكمبيوتر وأن جدار حماية ويندوز يسمح بالمنفذ 5000، أو اكتب العنوان يدويًا.");
                    } else {
                        ui.post(new Runnable() { public void run() { addressInput.setText(host); checkAndOpen(host, false); } });
                    }
                } catch (Exception e) {
                    status("تعذر البحث: " + e.getMessage());
                } finally {
                    scanning = false;
                    busy(false);
                }
            }
        }).start();
    }

    private static List<String> localPrefixes() throws Exception {
        List<String> out = new ArrayList<String>();
        for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
            if (!ni.isUp() || ni.isLoopback()) continue;
            for (InetAddress a : Collections.list(ni.getInetAddresses())) {
                if (a instanceof Inet4Address && a.isSiteLocalAddress()) {
                    String ip = a.getHostAddress();
                    String p = ip.substring(0, ip.lastIndexOf('.') + 1);
                    if (!out.contains(p)) out.add(p);
                }
            }
        }
        return out;
    }

    /** هل هذا العنوان خادم المنصة؟ (رد /api/status فيه screener_age وstream) */
    private static boolean isPlatform(String host, int connectMs, int readMs) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL("http://" + host + "/api/status").openConnection();
            c.setConnectTimeout(connectMs);
            c.setReadTimeout(readMs);
            if (c.getResponseCode() != 200) return false;
            InputStream in = c.getInputStream();
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] b = new byte[4096];
            int n;
            while ((n = in.read(b)) > 0 && buf.size() < 65536) buf.write(b, 0, n);
            String body = buf.toString("UTF-8");
            return body.contains("\"screener_age\"") && body.contains("\"stream\"");
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private void checkAndOpen(final String host, final boolean saved) {
        if (statusText != null) { status("جارٍ الاتصال بـ " + host + " …"); busy(true); }
        new Thread(new Runnable() {
            public void run() {
                final boolean ok = isPlatform(host, 4000, 8000);
                busy(false);
                ui.post(new Runnable() {
                    public void run() {
                        if (ok) {
                            server = host;
                            prefs.edit().putString(KEY_SERVER, host).apply();
                            openWeb();
                        } else if (saved) {
                            showSetup("تعذر الاتصال بالمنصة على " + host + ".\nهل المنصة تعمل على الكمبيوتر؟ وهل الجوال على نفس الواي فاي؟");
                        } else {
                            status("تعذر الاتصال بـ " + host + " — تأكد من العنوان ومن تشغيل المنصة");
                        }
                    }
                });
            }
        }).start();
    }

    // ===== عرض المنصة =====
    private void openWeb() {
        web = new WebView(this);
        web.setBackgroundColor(BG);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);  // المفضلة وصفقتي والإعدادات تُحفظ في التطبيق
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);  // صوت التنبيه
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        web.addJavascriptInterface(new Bridge(), "AndroidApp");
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                Uri u = Uri.parse(url);
                if (("http".equals(u.getScheme()) || "https".equals(u.getScheme())) && u.getAuthority() != null
                        && u.getAuthority().equals(server)) return false;
                // الروابط الخارجية (الأخبار، SEC، TradingView) تفتح في المتصفح
                try { startActivity(new Intent(Intent.ACTION_VIEW, u)); } catch (Exception ignored) { }
                return true;
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) {
                view.evaluateJavascript(NOTIFY_SHIM, null);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                view.evaluateJavascript(NOTIFY_SHIM, null);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) lost(String.valueOf(err.getDescription()));
            }

            @Override
            @SuppressWarnings("deprecation")
            public void onReceivedError(WebView view, int code, String description, String url) {
                if (Build.VERSION.SDK_INT < 23) lost(description);  // أندرويد 5–6 فقط
            }
        });
        setContentView(web, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        web.loadUrl("http://" + server + "/");
        invalidateOptionsMenu();
    }

    private void lost(String why) {
        showSetup("انقطع الاتصال بالمنصة (" + why + ").\nتأكد أن المنصة تعمل على الكمبيوتر وأن الجوال على نفس الواي فاي، ثم اضغط «اتصال».");
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) web.onResume();
    }

    @Override
    protected void onPause() {
        if (web != null) web.onPause();
        super.onPause();
    }

    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        menu.add(0, 1, 0, "تحديث");
        menu.add(0, 2, 1, "الصفحة الرئيسية");
        menu.add(0, 3, 2, "تغيير عنوان المنصة");
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        switch (item.getItemId()) {
            case 1:
                if (web != null) web.reload(); else if (server != null) checkAndOpen(server, true);
                return true;
            case 2:
                if (web != null) web.loadUrl("http://" + server + "/");
                return true;
            case 3:
                showSetup("العنوان الحالي: " + (server == null ? "—" : server));
                return true;
        }
        return super.onOptionsItemSelected(item);
    }

    // ===== الإشعارات =====
    private class Bridge {
        @JavascriptInterface
        public void notify(final String title, final String body) {
            ui.post(new Runnable() { public void run() { showNotification(title, body); } });
        }
    }

    private void showNotification(String title, String body) {
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null) return;
        Intent open = new Intent(this, MainActivity.class);
        open.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 23 ? 0x04000000 /* FLAG_IMMUTABLE */ : 0);
        PendingIntent pi = PendingIntent.getActivity(this, 0, open, flags);
        Notification.Builder b = builder();
        b.setSmallIcon(R.drawable.ic_stat).setContentTitle(title).setContentText(body)
            .setStyle(new Notification.BigTextStyle().bigText(body))
            .setAutoCancel(true).setContentIntent(pi).setDefaults(Notification.DEFAULT_ALL);
        nm.notify(notifId++, b.build());
        try {
            Vibrator v = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
            if (v != null) v.vibrate(250);
        } catch (Exception ignored) { }
    }

    /** Notification.Builder(Context, channelId) من أندرويد 8 فما فوق (عبر reflection لأن مكتبة البناء API 23). */
    private Notification.Builder builder() {
        if (Build.VERSION.SDK_INT >= 26) {
            try {
                return (Notification.Builder) Notification.Builder.class
                    .getConstructor(Context.class, String.class).newInstance(this, CHANNEL);
            } catch (Exception ignored) { }
        }
        return new Notification.Builder(this).setPriority(Notification.PRIORITY_HIGH);
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < 26) return;
        try {
            Class<?> ch = Class.forName("android.app.NotificationChannel");
            Object channel = ch.getConstructor(String.class, CharSequence.class, int.class)
                .newInstance(CHANNEL, "تنبيهات المنصة", 4 /* IMPORTANCE_HIGH */);
            NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
            NotificationManager.class.getMethod("createNotificationChannel", ch).invoke(nm, channel);
        } catch (Exception ignored) { }
    }

    private void askNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 1);
        }
    }

    // ===== أدوات =====
    private static String normalize(String s) {
        s = s.trim().replace("http://", "").replace("https://", "");
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        if (s.isEmpty()) return null;
        if (!s.contains(":")) s = s + ":" + PORT;
        return s;
    }

    private TextView text(String s, int sp, int color) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        t.setTextColor(color);
        t.setGravity(Gravity.START);
        t.setLineSpacing(0, 1.2f);
        return t;
    }

    private Button button(String s) {
        Button b = new Button(this);
        b.setText(s);
        b.setTextSize(16);
        b.setTextColor(Color.WHITE);
        b.setBackgroundColor(ACCENT);
        b.setAllCaps(false);
        return b;
    }

    private LinearLayout.LayoutParams lp(int top) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        p.topMargin = top;
        return p;
    }

    private int dp(int v) { return Math.round(v * getResources().getDisplayMetrics().density); }
}
