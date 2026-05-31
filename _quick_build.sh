#!/bin/bash
set -e

DIST=/root/sakubo_build/android/platform/build-arm64-v8a/dists/sakubo
ASSETS=$DIST/src/main/assets/private.tar
SRC=/root/sakubo_src
WORKDIR=/tmp/private_fix
HP=/root/sakubo_build/android/platform/build-arm64-v8a/build/other_builds/hostpython3/desktop/hostpython3/native-build/python3
WIN_SRC="/mnt/c/Users/Michael/Desktop/Spoonfed Japanese"

echo "=== Extracting current private.tar ==="
rm -rf $WORKDIR
mkdir -p $WORKDIR
cd $WORKDIR
zcat $ASSETS | tar xf -

echo "=== Syncing and recompiling changed source files ==="
# main.py
cp "$WIN_SRC/main.py" "$SRC/main.py"
cp "$SRC/main.py" main.py
$HP -c "import py_compile; py_compile.compile('main.py', 'main.pyc', doraise=True)"
rm -f main.py
echo "  + main.pyc"

# dictionary/db.py
cp "$WIN_SRC/dictionary/db.py" "$SRC/dictionary/db.py"
cp "$SRC/dictionary/db.py" dictionary/db.py
$HP -c "import py_compile; py_compile.compile('dictionary/db.py', 'dictionary/db.pyc', doraise=True)"
rm -f dictionary/db.py
echo "  + dictionary/db.pyc"

# dictionary/learning.py
cp "$WIN_SRC/dictionary/learning.py" "$SRC/dictionary/learning.py"
cp "$SRC/dictionary/learning.py" dictionary/learning.py
$HP -c "import py_compile; py_compile.compile('dictionary/learning.py', 'dictionary/learning.pyc', doraise=True)"
rm -f dictionary/learning.py
echo "  + dictionary/learning.pyc"

# dictionary/tts.py
cp "$WIN_SRC/dictionary/tts.py" "$SRC/dictionary/tts.py"
cp "$SRC/dictionary/tts.py" dictionary/tts.py
$HP -c "import py_compile; py_compile.compile('dictionary/tts.py', 'dictionary/tts.pyc', doraise=True)"
rm -f dictionary/tts.py
echo "  + dictionary/tts.pyc"

# app/ui.kv
cp "$WIN_SRC/app/ui.kv" "$SRC/app/ui.kv"
cp "$SRC/app/ui.kv" app/ui.kv
echo "  + app/ui.kv"

echo "=== Compiling handwriting_canvas ==="
cp "$SRC/handwriting_canvas.py" handwriting_canvas.py
$HP -c "import py_compile; py_compile.compile('handwriting_canvas.py', 'handwriting_canvas.pyc', doraise=True)"
rm -f handwriting_canvas.py
echo "  + handwriting_canvas.pyc"

echo "=== Compiling missing dictionary modules ==="
# Always sync paths.py from Windows source (it has platform-aware helpers)
cp "$WIN_SRC/dictionary/paths.py" "$SRC/dictionary/paths.py"
for mod in deinflect grammar_learning handwriting_drill leap_bridge paths pitch_accent sentence_generator stroke_order; do
    if [ ! -f "dictionary/${mod}.pyc" ] || [ "$mod" = "paths" ]; then
        echo "  + dictionary/${mod}.pyc"
        cp "$SRC/dictionary/${mod}.py" "dictionary/${mod}.py"
        $HP -c "import py_compile; py_compile.compile('dictionary/${mod}.py', 'dictionary/${mod}.pyc', doraise=True)"
        rm -f "dictionary/${mod}.py"
    fi
done

echo "=== Dictionary modules ==="
ls dictionary/*.pyc

echo "=== Compiling reading modules ==="
for mod in __init__ db_schema dictation processor translation; do
    cp "$WIN_SRC/reading/${mod}.py" "$SRC/reading/${mod}.py"
    cp "$SRC/reading/${mod}.py" "reading/${mod}.py"
    $HP -c "import py_compile; py_compile.compile('reading/${mod}.py', 'reading/${mod}.pyc', doraise=True)"
    rm -f "reading/${mod}.py"
    echo "  + reading/${mod}.pyc"
done

echo "=== Compiling sync modules ==="
for mod in sync supabase_client auth subscription; do
    cp "$WIN_SRC/sync/${mod}.py" "$SRC/sync/${mod}.py"
    cp "$SRC/sync/${mod}.py" "sync/${mod}.py"
    $HP -c "import py_compile; py_compile.compile('sync/${mod}.py', 'sync/${mod}.pyc', doraise=True)"
    rm -f "sync/${mod}.py"
    echo "  + sync/${mod}.pyc"
done

echo "=== Rebuilding gzipped private.tar ==="
tar cf - . | gzip -9 > $ASSETS
echo "  Size: $(ls -la $ASSETS | awk '{print $5}') bytes"
gzip -t $ASSETS && echo "  gzip OK"

# Update private_version
NEW_VERSION=$(md5sum $ASSETS | cut -d' ' -f1)_v$(date +%s)
sed -i "s|<string name=\"private_version\">.*</string>|<string name=\"private_version\">${NEW_VERSION}</string>|" $DIST/src/main/res/values/strings.xml
echo "  private_version updated"

# Verify libsqlite3.so is in place
echo "=== Checking libsqlite3.so ==="
ls -la $DIST/libs/arm64-v8a/libsqlite3.so

echo "=== Gradle assembleDebug ==="
cd $DIST
./gradlew assembleDebug 2>&1 | tail -5

APK=$(find $DIST/build -name '*.apk' -path '*/debug/*' | head -1)
if [ -n "$APK" ]; then
    cp "$APK" "$WIN_SRC/sakubo-debug.apk"
    echo "=== APK copied: $(ls -la "$APK" | awk '{print $5}') bytes ==="
else
    echo "FAILED - no APK"
    exit 1
fi
