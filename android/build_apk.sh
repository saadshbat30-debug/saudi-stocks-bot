#!/usr/bin/env bash
# يبني APK بأدوات Ubuntu (بدون Gradle وبدون خوادم Google):
#   apt-get install -y android-sdk-platform-23 aapt dalvik-exchange zipalign apksigner
# الاستخدام: KS_PASS=<كلمة مرور المفتاح> ./build_apk.sh   ← الناتج dist/stocks-platform.apk
set -euo pipefail
cd "$(dirname "$0")"
JAR=/usr/lib/android-sdk/platforms/android-23/android.jar
export KS_PASS="${KS_PASS:?ضع كلمة مرور مفتاح التوقيع في المتغير KS_PASS}"
rm -rf build && mkdir -p build/gen build/obj dist
aapt package -f -m -M AndroidManifest.xml -S res -I "$JAR" -J build/gen -F build/unsigned.apk
javac --release 8 -nowarn -Xlint:-options -encoding UTF-8 -cp "$JAR" -d build/obj \
  src/com/saad/stocks/*.java build/gen/com/saad/stocks/R.java
dalvik-exchange --dex --min-sdk-version=21 --output=build/classes.dex build/obj
(cd build && aapt add unsigned.apk classes.dex >/dev/null)
zipalign -f 4 build/unsigned.apk build/aligned.apk
# مفتاح التوقيع: نفس المفتاح في كل إصدار حتى يُحدَّث التطبيق دون حذفه (وتبقى المفضلة والإعدادات)
if [ ! -f stocks.keystore ]; then
  keytool -genkeypair -keystore stocks.keystore -storepass "$KS_PASS" -keypass "$KS_PASS" \
    -alias stocks -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Stocks Platform" >/dev/null 2>&1
fi
apksigner sign --ks stocks.keystore --ks-pass env:KS_PASS --key-pass env:KS_PASS \
  --ks-key-alias stocks --out dist/stocks-platform.apk build/aligned.apk
apksigner verify --verbose dist/stocks-platform.apk | head -5
ls -la dist/stocks-platform.apk
