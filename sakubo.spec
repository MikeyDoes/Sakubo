# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Sakubo (Windows one-folder build)
#
# Build command (from project root with .venv activated or using full path):
#   .venv\Scripts\pyinstaller.exe sakubo.spec
#
# The output is dist\Sakubo\ — zip that folder for website distribution.
# Run _clean_for_distribution.py BEFORE building to remove user data and the DB.

import os
from kivy_deps import sdl2, glew, angle
from kivy.tools.packaging.pyinstaller_hooks import get_deps_all, hookspath, runtime_hooks

block_cipher = None

# Kivy hidden imports and runtime hooks
_kivy_deps = get_deps_all()

a = Analysis(
    ['main.py'],
    pathex=[os.path.abspath('.')],
    binaries=[
        # kivy_deps DLLs (SDL2, GLEW, ANGLE)
        *[(f, '.') for f in sdl2.dep_bins],
        *[(f, '.') for f in glew.dep_bins],
        *[(f, '.') for f in angle.dep_bins],
    ],
    datas=[
        # KV layout files (loaded via Builder.load_file at runtime)
        ('app/ui.kv',        'app'),
        ('app/ui_light.kv',  'app'),
        # Fonts referenced directly by path in KV and Python code
        ('fonts',            'fonts'),
        # Lesson JSON files (loaded by path relative to CWD)
        ('dictionary/lessons/spoonfed_japanese', 'dictionary/lessons/spoonfed_japanese'),
        # Graded readings (N5) — used once reading UI is added
        ('readings',         'readings'),
        # Handwriting recogniser data
        ('kanjivg.zip',      '.'),
        # App icon (for window title bar; also used in onboarding)
        ('sakubo_icon.png',  '.'),
        # llama_cpp native DLLs (llama.dll, ggml*.dll) — loaded via ctypes at runtime
        ('.venv/Lib/site-packages/llama_cpp/lib', 'llama_cpp/lib'),
    ],
    hiddenimports=[
        *_kivy_deps.get('hiddenimports', []),
        # Local app packages
        'dictionary',
        'dictionary.db',
        'dictionary.paths',
        'dictionary.tts',
        'dictionary.learning',
        'dictionary.grammar_learning',
        'dictionary.handwriting_drill',
        'dictionary.deinflect',
        'dictionary.fsrs',
        'dictionary.leap_bridge',
        'dictionary.pitch_accent',
        'dictionary.stroke_order',
        'dictionary.voicevox',
        'dictionary.import_grammar',
        'dictionary.sentence_generator',
        'kanjivg_recognizer',
        'reading',
        'reading.translation',
        'reading.processor',
        'reading.dictation',
        'reading.db_schema',
        'sync',
        'sync.auth',
        'sync.sync',
        'sync.subscription',
        'sync.supabase_client',
        'app',
        'app.ui',
        'app.widgets',
        'app.widgets.stroke_order_widget',
        # Third-party hidden imports
        'llama_cpp',
        'llama_cpp._internals',
        'pyttsx3',
        'pyttsx3.drivers',
        'pyttsx3.drivers.sapi5',
        'supabase',
        'gotrue',
        'postgrest',
        'realtime',
        'storage3',
        # Kivy providers used on Windows
        'kivy.core.window.window_sdl2',
        'kivy.core.image.img_sdl2',
        'kivy.core.image.img_pil',
        'kivy.core.text.text_sdl2',
        'kivy.core.audio.audio_sdl2',
        'kivy.core.clipboard.clipboard_sdl2',
        'kivy.core.video.video_null',
        'kivy.core.spelling',
        # SQLite / encoding fallbacks
        'sqlite3',
        '_sqlite3',
        'encodings.utf_8',
        'encodings.ascii',
        'encodings.latin_1',
    ],
    hookspath=hookspath(),
    hooksconfig={},
    runtime_hooks=runtime_hooks() + ['rthook_llama_cpp.py'],
    excludes=[
        # Android-only, never needed on Windows
        'android', 'jnius', 'buildozer',
        # These large packages are not needed at runtime on desktop
        'IPython', 'jupyter', 'notebook',
        'matplotlib', 'pandas', 'numpy.distutils',
        'tkinter', 'test',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Sakubo',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,        # UPX can cause false-positive AV alerts; leave off for distribution
    console=False,    # No console window — GUI app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='sakubo_icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Sakubo',
)
