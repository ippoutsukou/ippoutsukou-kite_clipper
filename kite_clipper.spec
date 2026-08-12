# -*- mode: python ; coding: utf-8 -*-


# マルチスポーツ対応: kite_clipper.py は起動直後に resolve_sport_profile() を呼び、
# profiles/<sport>.json を読み込む。同梱しないと凍結時に ProfileError で終了コード2になり、
# GUI から呼ばれる CLI がすべて失敗する（multi_sport/MULTI_SPORT_SPEC.md 2.1 / 3.4）。
# onefile ビルドでは sys._MEIPASS（一時展開先）配下に展開され、sport_profile.py の
# 探索ロジックがそこを最優先で見る。
profile_datas = [('profiles', 'profiles')]

a = Analysis(
    ['kite_clipper.py'],
    pathex=[],
    binaries=[],
    datas=profile_datas,
    hiddenimports=['sport_profile'],
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
    a.binaries,
    a.datas,
    [],
    name='kite_clipper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
