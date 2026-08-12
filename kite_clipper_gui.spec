# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

sklearn_datas, sklearn_binaries, sklearn_hiddenimports = collect_all('sklearn')
onnxruntime_datas, onnxruntime_binaries, onnxruntime_hiddenimports = collect_all('onnxruntime')

# マルチスポーツ対応: profiles/*.json はアプリ本体の設置場所（app_base_dir()）を基準に
# 探索されるため、凍結ビルドでも exe と同じ場所（配布ルート）に同梱する必要がある
# （multi_sport/MULTI_SPORT_SPEC.md 2.1 / 3.4）。
profile_datas = [('profiles', 'profiles')]

a = Analysis(
    ['kite_clipper_gui.py'],
    pathex=[],
    binaries=sklearn_binaries + onnxruntime_binaries,
    datas=sklearn_datas + onnxruntime_datas + profile_datas,
    hiddenimports=[
        'frame_scorer',
        'clip_embedder',
        'sport_profile',
        *sklearn_hiddenimports,
        *onnxruntime_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='kite_clipper_gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='kite_clipper_gui',
)
