# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

sklearn_datas, sklearn_binaries, sklearn_hiddenimports = collect_all('sklearn')
onnxruntime_datas, onnxruntime_binaries, onnxruntime_hiddenimports = collect_all('onnxruntime')

# マルチスポーツ対応: PyInstaller 6.x の onedir ビルドでは datas はここで指定した
# 'profiles' フォルダとして _internal/profiles/ に展開される（sys._MEIPASS が指す場所）。
# exe と同じ場所（配布ルート）ではないので注意。sport_profile._resolve_profiles_dir() が
# 凍結時に _MEIPASS/profiles を優先探索するため、この配置で問題なく読み込める
# （見つからない場合は sys.executable と同じ場所の profiles/ もフォールバック探索する）。
# 一方 dataset/model はユーザーが書き込む領域のため、引き続き exe と同じ場所
# （app_base_dir()）を基準に解決する（sport_profile.load_profile() の root 引数）。
# 詳細: multi_sport/MULTI_SPORT_SPEC.md 2.1 / 3.4
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
