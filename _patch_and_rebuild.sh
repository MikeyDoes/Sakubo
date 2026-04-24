#!/bin/bash
set -e

# Build mode: "debug" (default) or "release"
BUILD_MODE="${1:-debug}"

DIST=/root/sakubo_build/android/platform/build-arm64-v8a/dists/sakubo

echo "=== Syncing source from Windows workspace ==="
WIN_SRC="/mnt/c/Users/Michael/Desktop/Spoonfed Japanese"
SAKUBO_SRC=/root/sakubo_src
cp "$WIN_SRC/main.py" "$SAKUBO_SRC/main.py"
cp "$WIN_SRC/app/ui.kv" "$SAKUBO_SRC/app/ui.kv"
cp "$WIN_SRC/dictionary/tts.py" "$SAKUBO_SRC/dictionary/tts.py"
cp "$WIN_SRC/dictionary/learning.py" "$SAKUBO_SRC/dictionary/learning.py"
cp "$WIN_SRC/dictionary/db.py" "$SAKUBO_SRC/dictionary/db.py"
cp "$WIN_SRC/dictionary/dictionary.db" "$SAKUBO_SRC/dictionary/dictionary.db"
cp "$WIN_SRC/dictionary/kanji_segments_cache.json" "$SAKUBO_SRC/dictionary/kanji_segments_cache.json"
cp "$WIN_SRC/reading/translation.py" "$SAKUBO_SRC/reading/translation.py"
cp "$WIN_SRC/reading/processor.py" "$SAKUBO_SRC/reading/processor.py"
cp "$WIN_SRC/reading/dictation.py" "$SAKUBO_SRC/reading/dictation.py"
cp "$WIN_SRC/reading/db_schema.py" "$SAKUBO_SRC/reading/db_schema.py"
cp "$WIN_SRC/reading/__init__.py" "$SAKUBO_SRC/reading/__init__.py"
cp "$WIN_SRC/handwriting_canvas.py" "$SAKUBO_SRC/handwriting_canvas.py"
mkdir -p "$SAKUBO_SRC/app/widgets"
cp "$WIN_SRC/app/widgets/stroke_order_widget.py" "$SAKUBO_SRC/app/widgets/stroke_order_widget.py"
mkdir -p "$SAKUBO_SRC/sync"
cp "$WIN_SRC/sync/sync.py" "$SAKUBO_SRC/sync/sync.py"
cp "$WIN_SRC/sync/supabase_client.py" "$SAKUBO_SRC/sync/supabase_client.py"
cp "$WIN_SRC/sync/auth.py" "$SAKUBO_SRC/sync/auth.py"
cp "$WIN_SRC/sync/subscription.py" "$SAKUBO_SRC/sync/subscription.py"
mkdir -p "$SAKUBO_SRC/p4a_recipes/billingbridge/src/com/sakubo"
cp "$WIN_SRC/p4a_recipes/billingbridge/src/com/sakubo/BillingBridge.java" "$SAKUBO_SRC/p4a_recipes/billingbridge/src/com/sakubo/BillingBridge.java"
mkdir -p "$SAKUBO_SRC/p4a_recipes/leapbridge/src/com/sakubo"
cp "$WIN_SRC/p4a_recipes/leapbridge/src/com/sakubo/LeapBridge.java" "$SAKUBO_SRC/p4a_recipes/leapbridge/src/com/sakubo/LeapBridge.java"

# Copy graded readings data (JSON files loaded at runtime)
for lvl in n5 n4 n3 n2 n1; do
    mkdir -p "$SAKUBO_SRC/readings/$lvl"
    cp "$WIN_SRC"/readings/$lvl/*.json "$SAKUBO_SRC/readings/$lvl/"
done

# graded_reading_map.json is user data — NOT bundled (each user builds their own)

# Sync lesson JSON files (kana, vocab, grammar lesson definitions)
echo "  Syncing lesson JSON files..."
LESSONS_SRC="$WIN_SRC/dictionary/lessons"
LESSONS_DST="$SAKUBO_SRC/dictionary/lessons"
# Top-level lesson index
cp "$LESSONS_SRC/index.json" "$LESSONS_DST/index.json"
cp "$LESSONS_SRC/jlpt_n5_vocab.json" "$LESSONS_DST/jlpt_n5_vocab.json"
# All spoonfed_japanese lesson files (kana, vocab, grammar, bonus, index, kana_vocab)
cp "$LESSONS_SRC"/spoonfed_japanese/*.json "$LESSONS_DST/spoonfed_japanese/"
# Grammar and vocab subdirectories per JLPT level
for sub in grammar_in_kana n1_grammar n1_vocab n2_grammar n2_vocab n3_grammar n3_vocab n4_grammar n4_vocab n5_grammar n5_vocab; do
    if [ -d "$LESSONS_SRC/spoonfed_japanese/$sub" ]; then
        mkdir -p "$LESSONS_DST/spoonfed_japanese/$sub"
        cp "$LESSONS_SRC/spoonfed_japanese/$sub/"*.json "$LESSONS_DST/spoonfed_japanese/$sub/" 2>/dev/null || true
    fi
done

echo "  Synced main.py, ui.kv, tts.py, learning.py, reading/*.py, stroke_order_widget.py, sync/*.py, readings/n5/, lessons/, BillingBridge.java"

echo "=== Patching build.gradle ==="
# compileSdkVersion 35 needed for LEAP SDK deps; targetSdkVersion 35 required by Play Store
# (edge-to-edge is handled via theme + fitsSystemWindows)
sed -i 's/targetSdkVersion 34/targetSdkVersion 35/' $DIST/build.gradle

# Sync versionCode/versionName from buildozer.spec
SPEC="$WIN_SRC/buildozer.spec"
NEW_VERSION=$(grep '^version = ' "$SPEC" | awk '{print $3}' | tr -d '\r')
# p4a auto-gen formula: "10" + minapi + version_code
# version 0.4 → 0*100+0=0, 0*100+4=4 → "10" + "31" + "4" = "10314"
VC=0
for i in $(echo "$NEW_VERSION" | tr '.' ' '); do
    VC=$((VC * 100 + i))
done
NEW_VC="1031${VC}"
sed -i "s/versionCode [0-9]*/versionCode $NEW_VC/" $DIST/build.gradle
# Use python for versionName to avoid shell quoting issues with sed + single quotes
python3 -c "
import re, sys
f = '$DIST/build.gradle'
txt = open(f).read()
txt = re.sub(r\"versionName ['\\\"][^'\\\"]*['\\\"]\", \"versionName '$NEW_VERSION'\", txt)
open(f,'w').write(txt)
"
echo "  Set versionCode=$NEW_VC versionName=$NEW_VERSION"

echo "=== Ensuring android-35 SDK platform exists ==="
SDK_PLAT=/root/.buildozer/android/platform/android-sdk/platforms
if [ -d "$SDK_PLAT/android-35.bak" ] && [ ! -d "$SDK_PLAT/android-35" ]; then
    mv "$SDK_PLAT/android-35.bak" "$SDK_PLAT/android-35"
    echo "  Restored android-35 from .bak"
fi

echo "=== Suppressing compileSdk warning ==="
GPROPS=$DIST/gradle.properties
if ! grep -q 'suppressUnsupportedCompileSdk' "$GPROPS" 2>/dev/null; then
    echo 'android.suppressUnsupportedCompileSdk=35' >> "$GPROPS"
    echo "  Added suppressUnsupportedCompileSdk=35"
fi

echo "=== Patching AndroidManifest.xml ==="
# Ensure targetSdkVersion is 35 in manifest too
sed -i 's/targetSdkVersion="34"/targetSdkVersion="35"/' $DIST/src/main/AndroidManifest.xml
# Remove debuggable from manifest — Gradle controls this via buildType
sed -i '/android:debuggable/d' $DIST/src/main/AndroidManifest.xml
# Remove unused FOREGROUND_SERVICE permissions (added by p4a template)
sed -i '/FOREGROUND_SERVICE/d' $DIST/src/main/AndroidManifest.xml
# Block foreground service permissions from dependencies via manifest merger
if ! grep -q 'xmlns:tools' $DIST/src/main/AndroidManifest.xml; then
    sed -i 's|xmlns:android="http://schemas.android.com/apk/res/android"|xmlns:android="http://schemas.android.com/apk/res/android"\n    xmlns:tools="http://schemas.android.com/tools"|' $DIST/src/main/AndroidManifest.xml
fi
if ! grep -q 'FOREGROUND_SERVICE.*tools:node="remove"' $DIST/src/main/AndroidManifest.xml; then
    sed -i '/<application/i\
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" tools:node="remove" />\
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_DATA_SYNC" tools:node="remove" />' $DIST/src/main/AndroidManifest.xml
fi
if ! grep -q 'SystemForegroundService.*tools:node="remove"' $DIST/src/main/AndroidManifest.xml; then
    sed -i '/<\/application>/i\
        <service android:name="androidx.work.impl.foreground.SystemForegroundService" tools:node="remove" />' $DIST/src/main/AndroidManifest.xml
fi

echo "=== values-v35 handled in splash theme section ==="

echo "=== Updating LeapBridge ==="
LEAP=$DIST/src/main/java/sakubo/com/sakubo/LeapBridge.java
SRC_LEAP=/root/sakubo_src/p4a_recipes/leapbridge/src/com/sakubo/LeapBridge.java
# Always copy latest source (overwrite old build copy)
if [ -f "$SRC_LEAP" ]; then
    mkdir -p "$(dirname "$LEAP")"
    cp "$SRC_LEAP" "$LEAP"
    echo "  Copied latest LeapBridge.java from source"
elif [ -f "${LEAP}.bak" ] && [ ! -f "$LEAP" ]; then
    mv "${LEAP}.bak" "$LEAP"
    echo "  Restored LeapBridge.java from .bak"
elif [ -f "$LEAP" ]; then
    echo "  LeapBridge.java already present (no source to update)"
else
    echo "  WARNING: LeapBridge.java not found anywhere!"
fi

echo "=== Updating BillingBridge ==="
BILLING=$DIST/src/main/java/sakubo/com/sakubo/BillingBridge.java
SRC_BILLING=/root/sakubo_src/p4a_recipes/billingbridge/src/com/sakubo/BillingBridge.java
if [ -f "$SRC_BILLING" ]; then
    mkdir -p "$(dirname "$BILLING")"
    cp "$SRC_BILLING" "$BILLING"
    echo "  Copied latest BillingBridge.java from source"
else
    echo "  WARNING: BillingBridge.java source not found!"
fi

echo "=== Injecting mavenCentral() into build.gradle ==="
# Add mavenCentral() after every jcenter() line (covers both buildscript and allprojects)
if ! grep -q 'mavenCentral()' $DIST/build.gradle; then
    sed -i '/jcenter()/a\        mavenCentral()' $DIST/build.gradle
    echo "  Added mavenCentral() to repositories"
else
    echo "  mavenCentral() already present"
fi

echo "=== Injecting LEAP dependencies ==="
if ! grep -q 'leap-sdk' $DIST/build.gradle; then
    sed -i "/^dependencies {/a\\
    implementation 'ai.liquid.leap:leap-sdk:0.9.7'\\
    implementation 'ai.liquid.leap:leap-model-downloader:0.9.7'\\
    implementation 'org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.1'" $DIST/build.gradle
    echo "  Added LEAP SDK dependencies"
else
    echo "  LEAP dependencies already present"
fi

echo "=== Injecting Billing dependency ==="
if ! grep -q 'billing' $DIST/build.gradle; then
    sed -i "/^dependencies {/a\\
    implementation 'com.android.billingclient:billing:7.1.1'" $DIST/build.gradle
    echo "  Added Google Play Billing dependency"
else
    echo "  Billing dependency already present"
fi

echo "=== Copying updated tts.py ==="
cp /root/sakubo_src/dictionary/tts.py /root/sakubo_build/android/app/dictionary/tts.py
echo "  Done"

echo "=== Copying updated learning.py ==="
cp /root/sakubo_src/dictionary/learning.py /root/sakubo_build/android/app/dictionary/learning.py
echo "  Done"

echo "=== Copying updated db.py ==="
cp /root/sakubo_src/dictionary/db.py /root/sakubo_build/android/app/dictionary/db.py
echo "  Done"

# Icons are synced from android_resources/ on every build.
# THIS FOLDER IS THE SINGLE SOURCE OF TRUTH. DO NOT REGENERATE ICONS FROM SOURCE PNGs.
# The correct icons are locked in git, extracted from the March 2026 Play Store AAB.
# If you need to verify: SHA256 of mipmap-xxxhdpi/ic_launcher_foreground.png must be
#   C7BD0D7A89CB4C79479D3901BF848B154B9F4E730F2BD1B2CF715EB2C235E449
RES=$DIST/src/main/res
echo "=== Verifying icons from android_resources/ ==="
ICON_SRC="$WIN_SRC/android_resources"
EXPECTED="c7bd0d7a89cb4c79479d3901bf848b154b9f4e730f2bd1b2cf715eb2c235e449"
ACTUAL=$(sha256sum "$ICON_SRC/mipmap-xxxhdpi/ic_launcher_foreground.png" | cut -d' ' -f1)
if [ "$ACTUAL" != "$EXPECTED" ]; then
    echo "ERROR: android_resources/mipmap-xxxhdpi/ic_launcher_foreground.png checksum mismatch!"
    echo "  Expected: $EXPECTED"
    echo "  Got:      $ACTUAL"
    echo "  The icons have been corrupted. Restore from: C:/Users/Michael/Desktop/sakubo-release.aab (March 2026)"
    exit 1
fi
echo "  Icon checksum OK"
echo "=== Syncing icons from android_resources/ ==="
for density in mipmap-hdpi mipmap-mdpi mipmap-xhdpi mipmap-xxhdpi mipmap-xxxhdpi mipmap; do
    if [ -d "$ICON_SRC/$density" ]; then
        cp "$ICON_SRC/$density/"*.png "$RES/$density/"
    fi
done
echo "  Icons synced"

echo "=== Updating private.tar (Python/KV app files) ==="
HP=/root/sakubo_build/android/platform/build-arm64-v8a/build/other_builds/hostpython3/desktop/hostpython3/native-build/python3
ASSETS=$DIST/src/main/assets/private.tar
WORKDIR=/tmp/private_repack
SRC=/root/sakubo_src

# Extract existing gzipped tar
rm -rf $WORKDIR
mkdir -p $WORKDIR
cd $WORKDIR
zcat $ASSETS | tar xf -

# Recompile main.pyc with p4a's hostpython (3.11.5)
echo "  Recompiling main.pyc..."
cp $SRC/main.py $WORKDIR/main.py
$HP -c "import py_compile; py_compile.compile('main.py', 'main.pyc', doraise=True)"
rm -f $WORKDIR/main.py

# Recompile reading/translation.pyc
echo "  Recompiling reading/translation.pyc..."
cp $SRC/reading/translation.py $WORKDIR/reading/translation.py
$HP -c "import py_compile; py_compile.compile('reading/translation.py', 'reading/translation.pyc', doraise=True)"
rm -f $WORKDIR/reading/translation.py

# Recompile reading/processor.pyc
echo "  Recompiling reading/processor.pyc..."
cp $SRC/reading/processor.py $WORKDIR/reading/processor.py
$HP -c "import py_compile; py_compile.compile('reading/processor.py', 'reading/processor.pyc', doraise=True)"
rm -f $WORKDIR/reading/processor.py

# Recompile reading/dictation.pyc
echo "  Recompiling reading/dictation.pyc..."
cp $SRC/reading/dictation.py $WORKDIR/reading/dictation.py
$HP -c "import py_compile; py_compile.compile('reading/dictation.py', 'reading/dictation.pyc', doraise=True)"
rm -f $WORKDIR/reading/dictation.py

# Recompile reading/db_schema.pyc
echo "  Recompiling reading/db_schema.pyc..."
cp $SRC/reading/db_schema.py $WORKDIR/reading/db_schema.py
$HP -c "import py_compile; py_compile.compile('reading/db_schema.py', 'reading/db_schema.pyc', doraise=True)"
rm -f $WORKDIR/reading/db_schema.py

# Copy reading/__init__.py (may be empty, just needs to exist)
cp $SRC/reading/__init__.py $WORKDIR/reading/__init__.py

# Copy graded readings JSON data
echo "  Copying readings JSON files (N5–N1)..."
for lvl in n5 n4 n3 n2 n1; do
    mkdir -p $WORKDIR/readings/$lvl
    cp $SRC/readings/$lvl/*.json $WORKDIR/readings/$lvl/
done

# graded_reading_map.json is user data — NOT bundled (each user builds their own)

# Copy lesson JSON files into private.tar
echo "  Copying lesson JSON files..."
LESSONS_SRC_TAR=$SRC/dictionary/lessons
cp "$LESSONS_SRC_TAR/index.json" "$WORKDIR/dictionary/lessons/index.json"
cp "$LESSONS_SRC_TAR/jlpt_n5_vocab.json" "$WORKDIR/dictionary/lessons/jlpt_n5_vocab.json"
cp "$LESSONS_SRC_TAR"/spoonfed_japanese/*.json "$WORKDIR/dictionary/lessons/spoonfed_japanese/"
for sub in grammar_in_kana n1_grammar n1_vocab n2_grammar n2_vocab n3_grammar n3_vocab n4_grammar n4_vocab n5_grammar n5_vocab; do
    if [ -d "$LESSONS_SRC_TAR/spoonfed_japanese/$sub" ]; then
        mkdir -p "$WORKDIR/dictionary/lessons/spoonfed_japanese/$sub"
        cp "$LESSONS_SRC_TAR/spoonfed_japanese/$sub/"*.json "$WORKDIR/dictionary/lessons/spoonfed_japanese/$sub/" 2>/dev/null || true
    fi
done

# Recompile dictionary/tts.pyc
echo "  Recompiling dictionary/tts.pyc..."
cp $SRC/dictionary/tts.py $WORKDIR/dictionary/tts.py
$HP -c "import py_compile; py_compile.compile('dictionary/tts.py', 'dictionary/tts.pyc', doraise=True)"
rm -f $WORKDIR/dictionary/tts.py

# Recompile dictionary/learning.pyc
echo "  Recompiling dictionary/learning.pyc..."
cp $SRC/dictionary/learning.py $WORKDIR/dictionary/learning.py
$HP -c "import py_compile; py_compile.compile('dictionary/learning.py', 'dictionary/learning.pyc', doraise=True)"
rm -f $WORKDIR/dictionary/learning.py

# Copy kanji segments cache (pre-computed for Android where pykakasi is unavailable)
echo "  Copying kanji_segments_cache.json..."
cp $SRC/dictionary/kanji_segments_cache.json $WORKDIR/dictionary/kanji_segments_cache.json

# Recompile dictionary/db.pyc
echo "  Recompiling dictionary/db.pyc..."
cp $SRC/dictionary/db.py $WORKDIR/dictionary/db.py
$HP -c "import py_compile; py_compile.compile('dictionary/db.py', 'dictionary/db.pyc', doraise=True)"
rm -f $WORKDIR/dictionary/db.py

# Recompile app/widgets/stroke_order_widget.pyc
echo "  Recompiling app/widgets/stroke_order_widget.pyc..."
mkdir -p $WORKDIR/app/widgets
cp $SRC/app/widgets/stroke_order_widget.py $WORKDIR/app/widgets/stroke_order_widget.py
$HP -c "import py_compile; py_compile.compile('app/widgets/stroke_order_widget.py', 'app/widgets/stroke_order_widget.pyc', doraise=True)"
rm -f $WORKDIR/app/widgets/stroke_order_widget.py

# Recompile handwriting_canvas.pyc
echo "  Recompiling handwriting_canvas.pyc..."
cp $SRC/handwriting_canvas.py $WORKDIR/handwriting_canvas.py
$HP -c "import py_compile; py_compile.compile('handwriting_canvas.py', 'handwriting_canvas.pyc', doraise=True)"
rm -f $WORKDIR/handwriting_canvas.py

# Copy ui.kv (plain text, no compilation needed)
echo "  Copying app/ui.kv..."
cp $SRC/app/ui.kv $WORKDIR/app/ui.kv

# Recompile sync/sync.pyc
echo "  Recompiling sync/sync.pyc..."
mkdir -p $WORKDIR/sync
cp $SRC/sync/sync.py $WORKDIR/sync/sync.py
$HP -c "import py_compile; py_compile.compile('sync/sync.py', 'sync/sync.pyc', doraise=True)"
rm -f $WORKDIR/sync/sync.py

# Recompile sync/supabase_client.pyc
echo "  Recompiling sync/supabase_client.pyc..."
cp $SRC/sync/supabase_client.py $WORKDIR/sync/supabase_client.py
$HP -c "import py_compile; py_compile.compile('sync/supabase_client.py', 'sync/supabase_client.pyc', doraise=True)"
rm -f $WORKDIR/sync/supabase_client.py

# Recompile sync/auth.pyc
echo "  Recompiling sync/auth.pyc..."
cp $SRC/sync/auth.py $WORKDIR/sync/auth.py
$HP -c "import py_compile; py_compile.compile('sync/auth.py', 'sync/auth.pyc', doraise=True)"
rm -f $WORKDIR/sync/auth.py

# Recompile sync/subscription.pyc
echo "  Recompiling sync/subscription.pyc..."
cp $SRC/sync/subscription.py $WORKDIR/sync/subscription.py
$HP -c "import py_compile; py_compile.compile('sync/subscription.py', 'sync/subscription.pyc', doraise=True)"
rm -f $WORKDIR/sync/subscription.py

# Rebuild gzipped tar (AssetExtract.java expects gzip format)
echo "  Rebuilding gzipped private.tar..."
cd $WORKDIR
tar cf - . | gzip -9 > $ASSETS
echo "  private.tar: $(ls -la $ASSETS | awk '{print $5}') bytes (gzip)"

# Update private_version to force re-extraction on device
NEW_VERSION=$(md5sum $ASSETS | cut -d' ' -f1)_v$(date +%s)
STRINGS_XML=$DIST/src/main/res/values/strings.xml
sed -i "s|<string name=\"private_version\">.*</string>|<string name=\"private_version\">${NEW_VERSION}</string>|" $STRINGS_XML
echo "  Updated private_version to $NEW_VERSION"

cd $DIST

echo "=== Verifying patches ==="
grep -n 'compileSdkVersion\|targetSdkVersion' $DIST/build.gradle

echo "=== Running gradle rebuild (mode: $BUILD_MODE) ==="
cd $DIST

if [ "$BUILD_MODE" = "release" ]; then
    # Inject release signing config into build.gradle
    if ! grep -q 'signingConfigs' $DIST/build.gradle; then
        sed -i '/android {/a\
    signingConfigs {\
        release {\
            storeFile file("/root/sakubo-release.jks")\
            storePassword "sakubo2026release"\
            storeType "PKCS12"\
            keyAlias "sakubo"\
            keyPassword "sakubo2026release"\
        }\
    }' $DIST/build.gradle
        echo "  Injected signing config block"
    fi
    # Ensure signingConfig reference exists in release buildType
    if ! grep -q 'signingConfig signingConfigs.release' $DIST/build.gradle; then
        python3 -c "
f = '$DIST/build.gradle'
with open(f) as fh:
    lines = fh.readlines()
in_bt = False
for i, line in enumerate(lines):
    if 'buildTypes {' in line:
        in_bt = True
    if in_bt and 'release {' in line:
        lines.insert(i + 1, '            signingConfig signingConfigs.release\n')
        break
with open(f, 'w') as fh:
    fh.writelines(lines)
"
        echo "  Injected signingConfig into release buildType"
    fi
    # Ensure debuggable false is set in release buildType
    if ! grep -q 'debuggable false' $DIST/build.gradle; then
        sed -i '/signingConfig signingConfigs.release/a\
            debuggable false' $DIST/build.gradle
        echo "  Set debuggable false in release buildType"
    fi
    ./gradlew clean bundleRelease

    echo "=== Copying AAB ==="
    AAB="$DIST/build/outputs/bundle/release/sakubo-release.aab"
    if [ -f "$AAB" ]; then
        cp "$AAB" /root/sakubo_build/bin/sakubo-release.aab
        ls -la /root/sakubo_build/bin/sakubo-release.aab
        echo "BUILD SUCCESS (release AAB)"
    else
        echo "BUILD FAILED - no AAB found"
        exit 1
    fi
else
    ./gradlew clean assembleDebug

    echo "=== Copying APK ==="
    APK=$(find $DIST/build -name '*.apk' -path '*/debug/*' | head -1)
    if [ -n "$APK" ]; then
        cp "$APK" /root/sakubo_build/bin/sakubo-0.2-arm64-v8a-debug.apk
        cp "$APK" "$WIN_SRC/sakubo-debug.apk"
        ls -la /root/sakubo_build/bin/sakubo-0.2-arm64-v8a-debug.apk
        echo "BUILD SUCCESS (debug APK)"
    else
        echo "BUILD FAILED - no APK found"
        exit 1
    fi
fi
