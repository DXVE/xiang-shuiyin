# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置(onefile, 无控制台)。

`datas` 必须把 `src/assets` 放到包根目录下的 `assets/`: 冻结后 `core/app.py` 以
`_MEIPASS/assets` 定位资源。位置错误会表现为字体回退到系统字体、界面图标与关于框 logo 缺失。

用法(在仓库根目录):
    .venv\\Scripts\\python.exe -m pip install -e ".[build]"
    .venv\\Scripts\\python.exe -m PyInstaller packaging\\xiangshuiyin-qt.spec
产物: dist/xiangshuiyin-qt.exe
"""
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent          # SPECPATH 由 PyInstaller 注入, 等于本文件所在目录

a = Analysis(
    [str(ROOT / "main.py")],                                     # 入口
    pathex=[str(ROOT / "src")],                                  # 让打包器找到 core / ui 两个包
    binaries=[],
    datas=[(str(ROOT / "src" / "assets"), "assets")],            # 资源 -> <_MEIPASS>/assets
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="xiangshuiyin-qt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,                       # 无控制台
    disable_windowed_traceback=False,
    icon=str(ROOT / "src" / "assets" / "icons" / "exif-watermark-icon.ico"),
)
