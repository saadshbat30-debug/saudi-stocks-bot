# saudi-stocks-bot

رادار للأسهم السعودية على صفحة ويب: أسعار قائمة متابعة، مؤشر تاسي، الأكثر ارتفاعاً وانخفاضاً،
وإشارات استراتيجية اختراق بولينجر باندز مع فلتر RSI. البيانات من [سهمك](https://www.sahmk.sa/developers).

## التشغيل

```bash
pip install -r requirements.txt
export SAHMK_API_KEY=shmk_live_...
python app.py   # ثم افتح http://localhost:5000
```

على Render: أمر التشغيل `gunicorn wsgi:app`، وأضف المتغيرات في Environment.

| المتغير | الوصف |
|---|---|
| `SAHMK_API_KEY` | مفتاح API من سهمك (مطلوب) |
| `SYMBOLS` | قائمة المتابعة الافتراضية، مثل `2222,1120,2010` |
| `BOT_TOKEN`, `CHAT_ID` | لإرسال الإشارات إلى تيليجرام عبر `/scan` |
| `CACHE_SECONDS` | مدة تخزين الأسعار مؤقتاً (افتراضياً 900 ثانية) |
| `BB_PERIODS`, `BB_DEVIATIONS`, `BB_MA_TYPE`, `BB_MAX_WIDTH_PCT`, `RSI_PERIOD`, `RSI_OVERBOUGHT`, `RSI_OVERSOLD` | إعدادات الاستراتيجية |

الباقة المجانية في سهمك (100 طلب يومياً) تعرض الأسعار وحركة السوق. إشارات بولينجر وRSI تحتاج
البيانات التاريخية المتاحة من باقة Starter.

## الصفحات

- `/` لوحة الأسهم، ويمكن تغيير القائمة: `/?symbols=2222,1120`
- `/scan` يفحص القائمة ويرسل إشارات اليوم إلى تيليجرام
- `/test` يرسل رسالة تجريبية إلى تيليجرام
- `/health` فحص أن الخدمة تعمل
